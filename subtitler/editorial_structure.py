"""Activity/state selections compiled into source-time removals.

Models choose named material. Only this compiler constructs executable ranges.
Utterances can cross visual boundaries; neither timeline owns the other.
"""
from __future__ import annotations

from typing import Any

from .errors import SubtitlerError
from .semantic_utterances import semantic_handle_bounds


def intersects(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a['start_ms'] < b['end_ms'] and b['start_ms'] < a['end_ms']


def activity_structure(source: dict[str, Any]) -> dict[str, Any]:
    """Keep canonical state boundaries and parent provenance, filling only gaps."""
    duration = source['duration_ms']
    semantic = source.get('stages', {}).get('semantic_spans', {}).get('output', {})
    nodes = semantic.get('event_graph', {}).get('nodes', [])
    episodes = [e for e in semantic.get('activity_episodes', []) if e.get('level') == 1]
    if not nodes:
        nodes = source.get('stages', {}).get('visual_learning', {}).get('output', {}).get('segments', [])
    boundaries = sorted({0, duration, *(max(0, min(duration, n[k])) for n in nodes for k in ('start_ms', 'end_ms')),
                         *(max(0, min(duration, e[k])) for e in episodes for k in ('start_ms', 'end_ms'))})
    activities: dict[str, dict[str, Any]] = {}
    states: list[dict[str, Any]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        span = {'start_ms': start, 'end_ms': end}
        parents = [e for e in episodes if intersects(e, span)]
        parent = parents[0] if parents else None
        aid = parent['episode_id'] if parent else f"{source['source_id']}-unassigned-{start}"
        if aid not in activities:
            activities[aid] = {'activity_id': aid, **span, 'label': parent['label'] if parent else 'Unassigned source transition',
                              'summary': parent.get('summary', '') if parent else 'No canonical activity assignment.', 'state_ids': []}
        activities[aid]['end_ms'] = end
        observed = [n for n in nodes if intersects(n, span)]
        state = {'state_id': f"{source['source_id']}-state-{len(states)+1:04d}", 'activity_id': aid, **span,
                 'observations': [n.get('observed_label', n.get('description', '')) for n in observed],
                 'canonical_event_ids': [n['event_id'] for n in observed if 'event_id' in n],
                 'uncertain': not observed}
        activities[aid]['state_ids'].append(state['state_id'])
        states.append(state)
    return {'activities': list(activities.values()), 'states': states}


def selection_schema(states: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    def obj(p: dict[str, Any]) -> dict[str, Any]:
        return {'type': 'object', 'properties': p, 'required': list(p), 'additionalProperties': False}
    text = {'type': 'string'}
    choice = {'type': 'string', 'enum': ['keep', 'omit']}
    def arr(items: dict[str, Any], count: int | None = None) -> dict[str, Any]:
        return {'type': 'array', 'items': items, **({'minItems': count, 'maxItems': count} if count is not None else {})}
    return obj({'summary': text, 'question': text,
        'states': arr(obj({'state_id': {'type': 'string', 'enum': [s['state_id'] for s in states]},
                          'decision': choice, 'contribution': text, 'transition_reason': text}), len(states)),
        'utterances': arr(obj({'unit_id': {'type': 'string', **({'enum': [u['unit_id'] for u in units]} if units else {})},
                              'decision': choice, 'reason': text,
                              'supporting_state_ids': arr(text)}), len(units)),
        'trim_pause_ids': arr(text)})


def validate_choices(result: dict[str, Any], states: list[dict[str, Any]], units: list[dict[str, Any]]) -> None:
    for key, identifier, expected in [('states', 'state_id', states), ('utterances', 'unit_id', units)]:
        values = [r[identifier] for r in result[key]]
        if len(values) != len(set(values)) or set(values) != {r[identifier] for r in expected}:
            raise SubtitlerError(f'Structured selection must account for every {key} exactly once')
    valid = {s['state_id'] for s in states}
    if any(ref not in valid for u in result['utterances'] for ref in u['supporting_state_ids']):
        raise SubtitlerError('Utterance support references an unassigned state')


def activity_packets(structure: dict[str, Any], units: list[dict[str, Any]], duration: int) -> list[dict[str, Any]]:
    """Bound calls by whole activities; a crossing thought joins adjacent work."""
    activities = structure['activities']
    state_ends = {s['end_ms'] for s in structure.get('states', [])}
    activity_ends = {a['end_ms'] for a in activities}
    safe = sorted(end for end in state_ends | activity_ends | {duration}
                  if not any(u['start_ms'] < end < u['end_ms'] for u in units))
    packets = []
    start = 0
    while start < duration:
        eligible = [end for end in safe if start < end <= start+480000]
        whole = [end for end in eligible if end in activity_ends]
        choices = whole or eligible or [end for end in safe if end > start][:1]
        if not choices:
            raise SubtitlerError('No complete activity/state boundary available for work packet')
        end = min(choices, key=lambda point: (abs(point-start-300000), -point))
        included = [a for a in activities if a['start_ms'] < end and a['end_ms'] > start]
        packets.append({'start_ms':start,'end_ms':end,
                        'passage_ids':[a['activity_id'] for a in included],
                        'split_parent':any(a['start_ms']<start or a['end_ms']>end for a in included)})
        start=end
    return packets


def plan_structure(source: dict[str, Any], utterances: dict[str, Any], facts: list[dict[str, Any]],
                   inspect: Any, identity: dict[str, Any], intent: dict[str, Any], guidance: Any,
                   director_model: str, director_effort: str, judge_model: str,
                   pauses: list[dict[str, Any]]) -> dict[str, Any]:
    """Two decision layers: activity contribution, then state/meaning selections."""
    structure = activity_structure(source)
    units = utterances['units']
    text = {'type':'string'}
    item = {'type':'object','properties':{'activity_id':text,
        'treatment':{'type':'string','enum':['preserve','condense','omit']},
        'contribution':text,'omission_cost':text,'edit_instruction':text},
        'required':['activity_id','treatment','contribution','omission_cost','edit_instruction'],'additionalProperties':False}
    schema = {'type':'object','properties':{'direction':text,'activities':{'type':'array','items':item,
        'minItems':len(structure['activities']),'maxItems':len(structure['activities'])}},
        'required':['direction','activities'],'additionalProperties':False}
    direction = inspect('activity_direction',director_model,
        {**identity,'intent':intent,'editorial_guidance':guidance,'activity_structure':structure,
         'utterance_meanings':[{k:u[k] for k in ('unit_id','start_ms','end_ms','meaning','dependency_unit_ids')} for u in units]},
        'Choose the narrative contribution and treatment of EVERY supplied activity exactly once by its activity_id. '
        'These are real task/attempt boundaries, not arbitrary processing windows. Compare how attempts develop '
        'learning, personality, effort, tension and outcomes under this project brief. No universal retry/menu policy. '
        'Identify the setup, execution and result that make selected activities worthwhile; a declaration alone is '
        'not a demonstration. Preserve when sustained experience matters, condense by naming useful states and '
        'transitions, omit only when its contribution and dependencies are dispensable. Utterance meanings are '
        'speech claims, not proof of game facts. Do not write narration or propose timestamps.',schema,[],8000,director_effort)
    ids = [a['activity_id'] for a in direction['activities']]
    if len(set(ids))!=len(ids) or set(ids)!={a['activity_id'] for a in structure['activities']}:
        raise SubtitlerError('Activity direction must cover each canonical activity exactly once')
    strategy = {'direction':direction['direction'],'priorities':[], 'passage_groups':[]}
    by_activity={a['activity_id']:a for a in structure['activities']}
    for row in direction['activities']:
        a=by_activity[row['activity_id']]
        strategy['passage_groups'].append({'label':a['label'],'relationship':'canonical activity',
            'comparison':row['contribution'],'passages':[{**a,**row,'passage_id':a['activity_id'],
            'evidence_ids':[f['id'] for f in facts if intersects(f,a)]}]})
    packets=activity_packets(structure,units,source['duration_ms'])
    decisions=[]
    for packet in packets:
        states=[s for s in structure['states'] if intersects(s,packet)]
        local_units=[u for u in units if intersects(u,packet)]
        evidence={**identity,'intent':intent,'editorial_guidance':guidance,
                  'activity_direction':direction,'assigned_states':states,'utterances':local_units,
                  'facts':[{k:v for k,v in f.items() if k!='provenance'} for f in facts if intersects(f,packet)],
                  'eligible_silent_pauses':[p for p in pauses if intersects(p,packet)]}
        result=inspect('select_states',judge_model,evidence,
            'Edit the assigned canonical states and complete semantic utterances through the activity direction. '
            'Account for EVERY state and utterance exactly once. Choose whole-state keep or omit, explaining '
            'its contribution and whether its transition must be witnessed. Choose whole-utterance keep or omit '
            'separately, considering linguistic dependencies and the viewing experience. Kept states protect '
            'overlapping utterances in full; kept utterances protect their own timing and dependent meanings. '
            'When a retained tactical plan needs demonstration, or a remark needs a visible referent, name the '
            'assigned supporting_state_ids that must remain. Do not retain disconnected declarations while '
            'omitting their meaningful execution/result. Preserve a readable menu transaction or omit it; avoid '
            'isolated contextless fragments. A speech bridge may legitimately cross visual-state boundaries. '
            'Do not assume every state change needs an explanation: use project intent and audience familiarity. '
            'No new narration, arbitrary timestamps, mandatory compression or invented outcomes. Reasons target '
            'the editor. Different attempts may deserve different treatments. Only trim a supplied eligible silent '
            'pause by its gap_id if its removal helps delivery without losing visible action or emotional timing; '
            'otherwise leave trim_pause_ids empty. Preserve natural breathing room. Missing or uncertain evidence '
            'is not evidence of no event. Set question only for a concrete unresolved consequential uncertainty.',
            selection_schema(states,local_units),[],12000,'medium')
        validate_choices(result,states,local_units)
        eligible = {p['gap_id'] for p in evidence['eligible_silent_pauses']}
        if set(result['trim_pause_ids']) - eligible:
            raise SubtitlerError('Selected pause was not supplied in this work packet')
        if result['question'].strip():
            # Uncertain local decisions retain material, explicitly recorded for manual review.
            for row in [*result['states'],*result['utterances']]:
                row['decision']='keep'
            result['trim_pause_ids']=[]
        decisions.append({**result,'start_ms':packet['start_ms'],'end_ms':packet['end_ms'],
                          'status':'unresolved_preserved' if result['question'].strip() else 'complete',
                          'selection_decisions':[]})
    normalized_pauses=[{**p,'pause_id':p['gap_id']} for p in pauses]
    proposals,restored=compile_selections(structure,units,decisions,facts,source['duration_ms'],normalized_pauses,
                                         semantic_artifact=utterances)
    return {'structure':structure,'selection_strategy':strategy,'decisions':decisions,'proposals':proposals,
            'dependency_restorations':restored,'work_packets':packets,
            'semantic_passages':[{**a,'passage_id':a['activity_id'],'purpose':a['summary']} for a in structure['activities']]}


def support_rejections(cuts: list[dict[str, Any]], units: list[dict[str, Any]],
                       structure: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, str]:
    """Recheck visual support after any later veto restores spoken material."""
    choices = {u['unit_id']:u for d in decisions for u in d['utterances']}
    kept = [u for u in units if not any(c['start_ms']<=u['start_ms'] and c['end_ms']>=u['end_ms'] for c in cuts)]
    needed = {ref for u in kept for ref in choices.get(u['unit_id'],{}).get('supporting_state_ids',[])}
    supports = [s for s in structure['states'] if s['state_id'] in needed]
    return {c['cut_id']:'Removal loses visual support required by a retained utterance'
            for c in cuts if any(intersects(c,s) for s in supports)}


def compile_selections(structure: dict[str, Any], units: list[dict[str, Any]],
                       decisions: list[dict[str, Any]], facts: list[dict[str, Any]], duration: int,
                       pauses: list[dict[str, Any]] | None = None, *,
                       leading_margin_ms: int = 750,
                       trailing_margin_ms: int = 1000,
                       semantic_artifact: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile explicit omissions, protecting complete thoughts and their support.

    Dependencies are closed before constructing cuts. Unknown states stay intact.
    Retained utterances use available breathing room without reviving independent
    omitted meanings. Actual overlapping speech extents remain inseparable.
    """
    state_choices = {x['state_id']: x for d in decisions for x in d['states']}
    unit_choices = {x['unit_id']: x for d in decisions for x in d['utterances']}
    by_unit = {u['unit_id']: u for u in units}
    keep_units = {u['unit_id'] for u in units if unit_choices.get(u['unit_id'], {}).get('decision', 'keep') == 'keep'}
    # Keeping any state also keeps every thought whose spoken extent touches it.
    keep_states = {s['state_id'] for s in structure['states']
                   if s['uncertain'] or state_choices.get(s['state_id'], {}).get('decision', 'keep') == 'keep'}
    for s in structure['states']:
        if s['state_id'] in keep_states:
            keep_units.update(u['unit_id'] for u in units if intersects(u, s))
    restored = []
    while True:
        prior = (len(keep_units), len(keep_states))
        for uid in list(keep_units):
            for ref in by_unit[uid].get('dependency_unit_ids', []):
                if ref not in by_unit:
                    raise SubtitlerError('Unknown semantic utterance dependency')
                if ref not in keep_units:
                    restored.append({'unit_id': ref, 'required_by': uid, 'reason': 'Retained utterance depends on this meaning'})
                keep_units.add(ref)
            keep_states.update(unit_choices.get(uid, {}).get('supporting_state_ids', []))
        protected = [(s['start_ms'], s['end_ms']) for s in structure['states'] if s['state_id'] in keep_states]
        protected += [(u['start_ms'], u['end_ms']) for u in units if u['unit_id'] in keep_units]
        # Source extents, dependencies and visual support require closure. Handles
        # are optional context and must never propagate preservation themselves.
        keep_units.update(u['unit_id'] for u in units if any(a < u['end_ms'] and b > u['start_ms'] for a,b in protected))
        if prior == (len(keep_units), len(keep_states)):
            break
    handles = semantic_handle_bounds(units, duration, leading_margin_ms, trailing_margin_ms)
    protected = [(s['start_ms'], s['end_ms']) for s in structure['states'] if s['state_id'] in keep_states]
    protected += [handles[uid] for uid in keep_units]
    merged: list[tuple[int, int]] = []
    for a, b in sorted(protected):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a,b))
    removals: list[dict[str, Any]] = []
    cursor = 0
    for a,b in [*merged,(duration,duration)]:
        if a-cursor >= 500:
            removals.append({'start_ms': cursor, 'end_ms': a, 'cut_style': 'outside_utterance'})
        cursor = b
    requested = {pid for d in decisions for pid in d['trim_pause_ids']}
    valid_pauses = {p['pause_id']: p for p in pauses or []}
    if requested - valid_pauses.keys():
        raise SubtitlerError('Unknown within-utterance pause selection')
    for pid in sorted(requested):
        p = valid_pauses[pid]
        if p['unit_id'] in keep_units:
            removals.append({**p, 'cut_style': 'within_utterance'})
    for c in removals:
        states = [s for s in structure['states'] if intersects(s,c)]
        c['state_ids'] = [s['state_id'] for s in states]
        c['activity_ids'] = list(dict.fromkeys(s['activity_id'] for s in states))
        c['reason'] = ('Shorten an explicitly selected verified silent pause without removing words.'
                       if c['cut_style']=='within_utterance' else ' / '.join(dict.fromkeys(
                           state_choices.get(s['state_id'],{}).get('contribution','Omitted state') for s in states)))
        c['evidence_ids'] = [f['id'] for f in facts if intersects(f,c)]
        c['requires_retained_ids'] = []
        c['boundary_provenance'] = []
        for edge in ('start_ms', 'end_ms'):
            timestamp = c[edge]
            available = bool(semantic_artifact and semantic_artifact.get('raw_vad_available'))
            active = [v for v in (semantic_artifact or {}).get('voice_activity', [])
                      if v['start_ms'] <= timestamp < v['end_ms']]
            adjacent = [u for u in units if u['start_ms'] == timestamp or u['end_ms'] == timestamp]
            c['boundary_provenance'].append({
                'edge': edge, 'timestamp_ms': timestamp,
                'basis': 'source_endpoint' if timestamp in (0, duration) else
                         'aligned_semantic_edge' if adjacent else 'bounded_natural_handle',
                'unit_ids': [u['unit_id'] for u in adjacent],
                'voice_evidence': 'active' if active else 'vad_clear' if available else 'unavailable',
                'voice_intervals': active,
                'review_required': timestamp not in (0, duration) and (bool(active) or not available),
                'limitation': 'Original alignment retained; VAD describes voice activity, not word ownership. No word timestamps were adjusted.'})
    return sorted(removals,key=lambda c:c['start_ms']), restored
