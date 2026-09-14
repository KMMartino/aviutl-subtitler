"""Independent, cached narration suggestions from completed editorial evidence."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .artifact_io import write_json_artifact
from .editorial_cutting import narration_actions_from_briefs
from .editorial_guidance import project_brief
from .editorial_locale import output_language_instruction
from .editorial_practices import EDITORIAL_PRACTICES_POLICY
from .editorial_recommendations import _activity_context, bounded_speech, catalog_overview
from .editorial_structure import intersects
from .errors import SubtitlerError
from .hosted_inspection import HostedInspectionProvider
from .operation_store import OperationStore, content_digest

NARRATION_VERSION = 2


def narration_inputs(project: dict[str, Any]) -> dict[str, Any]:
    recommendations = project['editorial_map']['editor_recommendations']
    identity = {'brief': project_brief(project), 'catalog': recommendations['catalog'],
                'language': project.get('processing_locale', 'en'),
                'target_duration_ms': [project.get('target_duration_min_ms'), project.get('target_duration_max_ms')], 'version': NARRATION_VERSION}
    return identity


def generate_narration(project: dict[str, Any], workspace: Path,
                       settings: dict[str, Any]) -> dict[str, Any]:
    if not settings.get('narration_enabled', True):
        return {'narration_briefs': [], 'api_cost_usd': 0, 'api_usage': []}
    recommendations = project['editorial_map']['editor_recommendations']
    identity = narration_inputs(project)
    revision = content_digest(identity)
    directory = workspace / revision
    usage = ApiUsageLedger()
    store = OperationStore(directory / 'operations', revision, usage)
    provider = HostedInspectionProvider(usage, float(settings.get('narration_budget_usd', 4)), directory / 'requests')
    model = str(settings.get('narration_model', 'gpt-5.6-terra'))
    overview = catalog_overview(recommendations['catalog'])
    briefs: list[dict[str, Any]] = []
    for source in recommendations['catalog']:
        activities = source['activities']
        for offset in range(0, len(activities), 6):
            group = activities[offset:offset + 6]
            activity_ids = {a['activity_id'] for a in group}
            states = [s for s in source['states'] if s['activity_id'] in activity_ids]
            if not states:
                continue
            all_states = states
            states = []
            for activity in group:
                children = [s for s in all_states if s['activity_id'] == activity['activity_id']]
                states.extend(children if len(children) <= 48 else [children[i * (len(children) - 1) // 47] for i in range(48)])
            by_id = {s['state_id']: s for s in states}
            text = {'type': 'string', 'minLength': 1}
            fields: dict[str, Any] = {k: text for k in ('purpose', 'memory_jog')}
            fields.update({k: {'type': 'array', 'items': text, 'minItems': 1, 'maxItems': 12}
                           for k in ('talking_points', 'representative_visuals')})
            fields['kind'] = {'type': 'string', 'enum': ['setup', 'mechanic_explanation',
                'causal_bridge', 'summary', 'callback', 'outcome_context']}
            fields.update({k: {'type': 'string', 'enum': list(by_id)} for k in ('first_state_id', 'last_state_id')})
            schema = {'type': 'object', 'properties': {'suggestions': {'type': 'array',
                'items': {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}}},
                'required': ['suggestions'], 'additionalProperties': False}
            speech = [row for row in source['speech'] if intersects(row,
                {'start_ms': group[0]['start_ms'], 'end_ms': group[-1]['end_ms']})]
            selected = bounded_speech(speech, 18000)
            evidence = {'brief': identity['brief'], 'target_duration_ms': identity['target_duration_ms'],
                'activities': [{k: v for k, v in a.items() if k != 'state_ids'} for a in group],
                **_activity_context(overview, group[0]),
                'states': states, 'state_context_is_sampled': len(states) < len(all_states), 'spoken_context': sorted(selected, key=lambda row: row['start_ms']),
                'spoken_context_is_sampled': len(selected) < len(speech),
                'previous_narration_briefs': [{'purpose': b['purpose'], 'memory_jog': b['memory_jog']}
                    for b in briefs[-8:]]}
            prompt = (
                'Prepare selective post-recorded narration BRIEFS for the creator, not finished voiceover scripts. '
                'The human chooses cuts and narration ranges in the EXO, then reimports it for factual reference material. '
                'Use the recording objective and target duration to understand where omitted travel, retries, or processes '
                'might need a concise explanatory passage. Do not treat speech-gap markers as approved cuts. '
                'Return an empty list if retained source audio and footage already do the job. '
                'One cohesive brief per continuous explanatory idea; merge adjacent ideas and avoid repeating previous briefs. '
                'A brief may span multiple adjacent activities in this source when they form one explanatory passage. '
                'purpose: the specific viewer knowledge gap or connection this narration would provide. '
                'memory_jog: a concise factual reminder of what happened for the creator. '
                'talking_points: factual bullet points the creator could cover in their own words. '
                'representative_visuals: concrete supplied moments that demonstrate those points, including original-audio '
                'reactions or payoffs worth retaining. Do not invent external facts, motivation, outcomes or footage. '
                'Do not write first-person read-aloud drafts, prescribe exact cuts, or dictate a finished edit. '
                'Choose the supplied first/last state IDs bounding each proposed source range. These are editable suggestions, '
                'not automatic removals. Keep prose concise and factual. '
                + EDITORIAL_PRACTICES_POLICY + '\n'
                + output_language_instruction(str(project.get('processing_locale') or 'en'))
                + '\n' + json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))
            )

            def decode(payload: dict[str, Any]) -> list[dict[str, Any]]:
                result = []
                for row in payload['suggestions']:
                    first, last = by_id[row['first_state_id']], by_id[row['last_state_id']]
                    if first['start_ms'] > last['start_ms']:
                        raise SubtitlerError('Narration must follow source order')
                    if any(not row[k].strip() for k in ('purpose', 'memory_jog')) or any(
                        not row[k] or any(not point.strip() for point in row[k])
                        for k in ('talking_points', 'representative_visuals')):
                        raise SubtitlerError('Narration requires factual reminders, talking points and footage suggestions')
                    result.append({**row, 'source_id': source['source_id'], 'activity_id': first['activity_id'],
                                   'start_ms': first['start_ms'], 'end_ms': last['end_ms']})
                result.sort(key=lambda r: r['start_ms'])
                if any(a['end_ms'] > b['start_ms'] for a, b in zip(result, result[1:])):
                    raise SubtitlerError('Narration suggestions overlap')
                return result

            try:
                rows = store.execute('narration_suggestions', NARRATION_VERSION, {'prompt': prompt, 'schema': schema, 'model': model},
                    lambda: provider.inspect(operation='narration_suggestions', model=model, prompt=prompt,
                        schema=schema, max_output_tokens=4000, reasoning_effort='low'), decode)
            except Exception as exc:
                setattr(exc, 'editorial_failure_output', {'api_cost_usd': usage.total_cost_usd})
                raise
            briefs.extend(rows)
            print(f"Narration: {source['name']} activities {offset + 1}-{offset + len(group)}; {len(rows)} suggestions; ${usage.total_cost_usd:.4f}", flush=True)
    for index, brief in enumerate(briefs, 1):
        brief['id'] = f'narration-{index:03d}'
    artifact = {'type': 'narration_suggestions', 'schema_version': NARRATION_VERSION, 'input_revision': revision,
                'narration_briefs': briefs, 'api_cost_usd': usage.total_cost_usd,
                'api_usage': [asdict(row) for row in usage.rows]}
    write_json_artifact(directory / 'narration.json', artifact)
    return artifact


def apply_narration(project: dict[str, Any], artifact: dict[str, Any]) -> None:
    editorial = project['editorial_map']
    editorial['narration_briefs'] = artifact['narration_briefs']
    editorial['final_actions'] = narration_actions_from_briefs(artifact['narration_briefs'])
    project['narration_artifact'] = artifact
