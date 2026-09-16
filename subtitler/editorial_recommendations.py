"""Evidence-indexed editor assessments. This artifact cannot encode executable cuts."""
from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .artifact_io import write_json_artifact
from .editorial_guidance import EDITORIAL_GUIDANCE, project_brief
from .editorial_locale import output_language_instruction
from .editorial_structure import activity_structure, intersects
from .errors import SubtitlerError
from .hosted_inspection import HostedInspectionProvider
from .operation_store import OperationStore, content_digest


def evidence_catalog(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain canonical provenance; no semantic-word reconstruction is necessary."""
    catalog = []
    activity_index = 0
    for source in project['sources']:
        structure = activity_structure(source)
        for activity in structure['activities']:
            activity_index += 1
            activity['display_id'] = f'A{activity_index:02}'
            for state_index, state in enumerate((s for s in structure['states'] if s['activity_id'] == activity['activity_id']), 1):
                state['display_id'] = f'{activity["display_id"]}.S{state_index:02}'
        result = source.get('result') or {}
        # speech_segments are acoustic timing intervals and normally have no text.
        # Keep those in gap detection; recommendations need the spoken utterances.
        speech = result.get('utterance_groups') or result.get('speech_segments') or []
        catalog.append({'source_id': source['source_id'], 'name': source.get('original_name', source['source_id']),
                        **structure, 'speech': [{'evidence_id': f"{source['source_id']}-speech-{i}",
                            'start_ms': row['start_ms'], 'end_ms': row['end_ms'],
                            'text': row.get('text', '')} for i, row in enumerate(speech)]})
    return catalog


def factual_overview(project: dict[str, Any]) -> dict[str, Any]:
    """Reuse collected activity summaries instead of generating an automatic edit."""
    return {'workflow': 'human_information', 'event_phases': [
        {**activity, 'source_id': source['source_id']} for source in evidence_catalog(project)
        for activity in source['activities']], 'global_threads': [], 'narration_briefs': [],
        'progression_summary': 'Activity and state observations are available below; recommendations do not change cut markers.',
        'api_cost_usd': 0, 'api_usage': []}


def catalog_overview(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overview = []
    project_offset = 0
    for source in catalog:
        overview.extend({'source_id': source['source_id'], **a,
            'project_start_ms': project_offset + a['start_ms'], 'project_end_ms': project_offset + a['end_ms']}
            for a in source['activities'])
        project_offset += max((a['end_ms'] for a in source['activities']), default=0)
    return overview


def _activity_context(overview: list[dict[str, Any]], activity: dict[str, Any]) -> dict[str, Any]:
    """One hour of recent context; older material becomes a bounded factual digest."""
    index = next(i for i, row in enumerate(overview) if row['activity_id'] == activity['activity_id'])
    cutoff = overview[index]['project_start_ms'] - 3_600_000
    recent = [i for i in range(index) if overview[i]['project_end_ms'] > cutoff]
    # Dense footage still gets bounded context, with the closest activities intact.
    sampled = recent if len(recent) <= 20 else sorted(set(
        [recent[i * (len(recent) - 1) // 15] for i in range(16)] + recent[-4:]))
    chosen = sampled + list(range(index, min(len(overview), index + 3)))
    rows = [{k: row[k] for k in ('source_id', 'activity_id', 'start_ms', 'end_ms')} |
            {'label': row['label'][:160], 'summary': row.get('summary', '')[:400]}
            for i in chosen for row in [overview[i]]]
    older = [row for row in overview[:index] if row['project_end_ms'] <= cutoff]
    digest = []
    # Extractive compaction: counts plus beginning/end observations, never an
    # invented account of outcomes. No old state lists or utterances enter it.
    groups = min(8, len(older))
    for i in range(groups):
        group = older[i * len(older) // groups:(i + 1) * len(older) // groups]
        digest.append({'activity_count': len(group),
            'recurring_activities': [{'label': label[:100], 'count': count}
                for label, count in Counter(r['label'] for r in group).most_common(4)],
            'first_observation': group[0].get('summary', '')[:160],
            'last_observation': group[-1].get('summary', '')[:160]})
    return {'related_activity_context': rows,
            'recent_context_is_sampled': len(sampled) < len(recent),
            'earlier_context_digest': digest,
            'earlier_context_is_lossy': bool(older)}


def bounded_speech(rows: list[dict[str, Any]], character_budget: int) -> list[dict[str, Any]]:
    """Complete utterances spread across the range, including its opening and end."""
    rows = sorted(rows, key=lambda row: row['start_ms'])
    if not rows:
        return []
    order = [0] + ([len(rows) - 1] if len(rows) > 1 else [])
    pending = deque([(1, len(rows) - 2)])
    while pending:
        start, end = pending.popleft()
        if start <= end:
            middle = (start + end) // 2
            order.append(middle)
            pending.extend(((start, middle - 1), (middle + 1, end)))
    selected, characters = [], 0
    for index in order:
        row = rows[index]
        if characters + len(row['text']) <= character_budget:
            selected.append(row)
            characters += len(row['text'])
    return sorted(selected, key=lambda row: row['start_ms'])


def _schema(ids: list[str], evidence_ids: list[str], related_ids: list[str]) -> dict[str, Any]:
    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    text = {'type': 'string'}
    def refs(values: list[str]) -> dict[str, Any]:
        return {'type': 'array', 'items': {'type': 'string', **({'enum': values} if values else {})}, 'maxItems': 5}
    row = obj({'target_id': {'type': 'string', 'enum': ids},
        'suggested_treatment': {'type': 'string', 'enum': ['retain', 'shorten', 'possible_omission', 'inspect']},
        'recommendation': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        'observed_content': text, 'potential_contribution': text, 'reason_and_tradeoff': text,
        'evidence_limitation': text, 'evidence_ids': refs(evidence_ids), 'related_ids': refs(related_ids)})
    return obj({'assessments': {'type': 'array', 'items': row, 'minItems': len(ids), 'maxItems': len(ids)}})


def generate_recommendations(project: dict[str, Any], workspace: Path, settings: dict[str, Any],
                             usage: ApiUsageLedger, provider: Any = None) -> dict[str, Any]:
    catalog = evidence_catalog(project)
    overview = catalog_overview(catalog)
    identity = {'catalog': catalog, 'brief': project_brief(project), 'locale': project.get('output_locale', 'en')}
    revision = content_digest(identity)
    store = OperationStore(workspace / 'recommendation-operations' / revision, revision, usage)
    provider = provider or HostedInspectionProvider(usage, workspace / 'recommendation-requests')
    model = str(settings.get('recommendation_model', 'gpt-5.6-terra'))
    assessments = []
    for source in catalog:
        for activity in source['activities']:
            states = [s for s in source['states'] if s['activity_id'] == activity['activity_id']]
            context = _activity_context(overview, activity)
            related = [a['activity_id'] for a in context['related_activity_context']]
            # One assessment owns the whole activity. State observations are evidence,
            # not separately commissioned editorial decisions.
            outline = states if len(states) <= 48 else [states[i * (len(states) - 1) // 47] for i in range(48)]
            batch = outline
            ids = [activity['activity_id']]
            related += [s['state_id'] for s in batch]
            span = activity
            speech = [s for s in source['speech'] if intersects(s, span)]
            selected = bounded_speech(speech, 12000)
            evidence_ids = [s['state_id'] for s in batch] + [s['evidence_id'] for s in selected]
            evidence = {'brief': identity['brief'], **context,
                'activity': {k: v for k, v in activity.items() if k != 'state_ids'},
                'states': batch, 'speech': selected,
                'activity_state_outline': [{'state_id': s['state_id'], 'start_ms': s['start_ms'],
                    'end_ms': s['end_ms'], 'observations': ' / '.join(s['observations'])[:400]} for s in outline],
                'activity_outline_is_partial': len(outline) < len(states),
                'speech_evidence_truncated': len(selected) != len(speech),
                'editorial_principles': EDITORIAL_GUIDANCE['principles']}
            schema = _schema(ids, evidence_ids, related)
            prompt = ('Assist the human EDITOR with a brief assessment of every requested target ID. '
                'The recommendation field is the prominent instruction the editor reads while working in a video editor: '
                'write a direct editing instruction, normally 6-18 English words or similarly compact in the output language. '
                'Do not include the treatment label; the report adds Keep / Shorten / Review. '
                'Name the concrete moment to keep or remove. Use plain verbs and everyday nouns, not an analytical justification. '
                'Style examples ONLY, not facts or universal editing rules: '
                '"Keep the preparation and one search; trim repeated checking." '
                '"Remove the second inventory screen; it repeats the first." '
                '"Keep the herb discovery; trim the rest of the search." '
                '"Keep the failed attempt: it shows why the next strategy changes." '
                'Avoid phrases such as preparation claim, tactical beat, challenge arc, supports the setup, '
                'validates the detour, or preserves chronology. Say what actually happens. '
                'Commit to a recommendation when evidence supports it; do not soften every instruction with may, might, or likely. '
                'If the deciding fact is unknown, use inspect and say exactly what to check instead of inventing certainty. '
                'Before returning, reread EVERY headline: can an editor act on it immediately without translating jargon? '
                'Rewrite any headline that only explains value, repeats the scene label, or needs its sidebar to make sense. '
                'Return exactly ONE recommendation for the ACTIVITY: its arc, pacing, relationship to other attempts, and useful outcome. '
                'use the activity outline, not just the current batch. If the outline is sampled, do not claim exhaustive knowledge. Do not repeat a list of local trims. '
                'You may reference supplied state IDs within the activity recommendation and supporting explanation to locate key moments. '
                'Do not produce a recommendation for every state or a checklist of all states. '
                'Use recognizable content landmarks, not numerical cut timestamps, and preserve complete spoken thoughts. '
                'These are human-editing suggestions, never executable cuts. Do not invent a landmark missing from the evidence. '
                'Observations are fallible sampled evidence, speech is a claim, and activity summaries are not ground truth. '
                'Separate observed content from editorial value. Suggest a rough treatment and explain the contribution '
                'and what could be lost. Compare related attempts using supplied evidence; repetition and menus are not '
                'universally dispensable. A quiet state may be valuable. Do not invent outcomes or missing context. '
                'Use inspect when consequential evidence is insufficient. Cite supplied evidence IDs and related activity or state IDs. '
                'Keep supporting prose fields to one short, plain sentence; avoid editorial jargon there too. Do not repeat the headline. '
                'Leave evidence_limitation empty when none is specific. '
                'No precise cuts, narrator instructions, scripts, mandatory preservation, or commands to the viewer. '
                + output_language_instruction(identity['locale']) + '\n' + json.dumps(evidence, ensure_ascii=False))

            def decode(value: Any) -> list[dict[str, Any]]:
                rows = value['assessments']
                if len(rows) != len(ids) or {r['target_id'] for r in rows} != set(ids):
                    raise SubtitlerError('Recommendations must cover each assigned target exactly once')
                for row in rows:
                    if set(row['evidence_ids']) - set(evidence_ids) or set(row['related_ids']) - set(related):
                        raise SubtitlerError('Recommendation cites unavailable evidence')
                return rows

            rows = store.execute('activity_assessment', 5, {'prompt': prompt, 'schema': schema, 'model': model},
                lambda: provider.inspect(operation='activity_assessment', model=model, prompt=prompt,
                    schema=schema, max_output_tokens=1800, reasoning_effort='low'), decode)
            assessments.extend({'source_id': source['source_id'], **row} for row in rows)
    artifact = {'schema_version': 2, 'type': 'editor_recommendations', 'input_revision': revision,
                'catalog': catalog, 'assessments': assessments, 'executable': False}
    write_json_artifact(workspace / 'editor-recommendations.json', artifact)
    return artifact

