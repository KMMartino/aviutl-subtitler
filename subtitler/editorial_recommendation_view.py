"""Readable editing notes keyed to the guide markers in the video editor."""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .artifact_io import write_json_artifact
from .editorial_locale import locale_label
from .editorial_structure import intersects
from .operation_store import content_digest


def _identifier_pattern(labels: dict[str, str]) -> re.Pattern[str] | None:
    # A single letter cannot be distinguished from ordinary prose.
    labels = {key: value for key, value in labels.items() if len(key) > 1}
    if not labels:
        return None
    return re.compile(r'(?<![A-Za-z0-9_:-])(?:' +
                      '|'.join(re.escape(key) for key in sorted(labels, key=len, reverse=True)) +
                      r')(?![A-Za-z0-9_:-])')


def marker_labels(artifact: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    activity_index = 0
    for source in artifact.get('catalog', []):
        for activity in source['activities']:
            activity_index += 1
            label = f'A{activity_index:02}'
            labels[activity['activity_id']] = label
            children = [s for s in source['states'] if s['activity_id'] == activity['activity_id']]
            for index, state in enumerate(children, 1):
                labels[state['state_id']] = f'{label}.S{index:02}'
    return labels


def _reference_labels(artifact: dict[str, Any], labels: dict[str, str]) -> dict[str, str]:
    """Resolve saved per-source display aliases to the actual EXO marker labels."""
    references = dict(labels)
    for source in artifact.get('catalog', []):
        for key, group in (('activity_id', 'activities'), ('state_id', 'states')):
            for row in source[group]:
                if row.get('display_id'):
                    references[row['display_id']] = labels[row[key]]
    return references


def write_recommendation_frames(report_path: Path, project: dict[str, Any]) -> dict[str, str]:
    """Prefer source-bound saved observations; extract only absent segment images."""
    sources = {s['source_id']: s for s in project['sources']}
    catalog = project.get('editorial_map', {}).get('editor_recommendations', {}).get('catalog', [])
    directory = report_path.with_name(report_path.stem + '-segment-frames')
    locks: dict[str, threading.Lock] = {}

    def capture(job: tuple[dict[str, Any], dict[str, Any], str]) -> dict[str, Any]:
        source, segment, identifier = job
        media = Path(source['visual_path'])
        stamp = (segment['start_ms'] + segment['end_ms']) // 2
        record = {'target_id': segment[identifier], 'timestamp_ms': stamp, 'path': None}
        if not media.is_file():
            return {**record, 'status': 'source_unavailable'}
        fingerprint = source.get('visual_fingerprint') or source.get('fingerprint')
        candidates = [r for r in source.get('reference_frames', [])
                      if fingerprint and r.get('visual_fingerprint') == fingerprint
                      and segment['start_ms'] <= r['timestamp_ms'] < segment['end_ms']
                      and Path(r['path']).is_file()]
        saved = min(candidates, key=lambda r: abs(r['timestamp_ms'] - stamp)) if candidates else None
        if saved:
            stamp = saved['timestamp_ms']
        stat = media.stat()
        key = content_digest([str(media.resolve()), stat.st_size, stat.st_mtime_ns, stamp,
                              str(saved['path']) if saved else 'extracted'])
        target = directory / (key + '.jpg')
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with locks.setdefault(key, threading.Lock()):
                if not target.is_file() or not target.stat().st_size:
                    if saved:
                        shutil.copy2(saved['path'], target)
                    else:
                        result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(stamp / 1000),
                            '-i', str(media), '-frames:v', '1', '-vf', 'scale=640:-2', '-y', str(target)],
                            capture_output=True, timeout=30)
                        if result.returncode or not target.is_file() or not target.stat().st_size:
                            target.unlink(missing_ok=True)
                            return {**record, 'status': 'extraction_failed'}
        except (OSError, subprocess.SubprocessError):
            target.unlink(missing_ok=True)
            return {**record, 'status': 'image_unavailable'}
        return {**record, 'timestamp_ms': stamp, 'path': directory.name + '/' + target.name,
                'status': 'reused' if saved else 'extracted'}

    # States remain timeline landmarks; the report needs images only for visible cards.
    narration_states = {b.get('first_state_id') for b in project.get('editorial_map', {}).get('narration_briefs', [])}
    jobs = [(sources[s['source_id']], row, key) for s in catalog
            for group, key in [('activities', 'activity_id'), ('states', 'state_id')] for row in s[group]
            if group == 'activities' or row[key] in narration_states]
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(capture, jobs))
    if records:
        write_json_artifact(directory / 'manifest.json', {'schema_version': 1, 'frames': records})
    return {r['target_id']: r['path'] for r in records if r['path']}


def recommendation_html(artifact: dict[str, Any], images: dict[str, str] | None = None, locale: str = 'en',
                        source_parts: dict[str, str] | None = None,
                        narration_notes: dict[str, str] | None = None) -> str:
    labels = marker_labels(artifact)
    assessments = {(r['source_id'], r['target_id']): r for r in artifact.get('assessments', [])}
    activities = {a['activity_id']: a for s in artifact.get('catalog', []) for a in s['activities']}
    segments = {**activities, **{s['state_id']: s for source in artifact.get('catalog', []) for s in source['states']}}
    replacements = _reference_labels(artifact, labels)
    for source in artifact.get('catalog', []):
        for row in source['speech']:
            replacements[row['evidence_id']] = locale_label(locale, 'the spoken comment', '発言')

    identifier_pattern = _identifier_pattern(replacements)

    def prose(value: Any) -> str:
        text = str(value or '')
        if identifier_pattern is not None:
            text = identifier_pattern.sub(lambda match: replacements[match[0]], text)
        for aid, label in labels.items():
            match = re.search(r':episode:(\d+)$', aid)
            if match:
                text = re.sub(r'\bepisode\s+' + match[1] + r'\b', label, text, flags=re.I)
        text = re.sub(r'\bsource-[\w:-]+', locale_label(locale, 'another segment', '別の区間'), text)
        text = text.replace('Unassigned source transition', locale_label(locale, 'Transition', '場面転換'))
        return html.escape(text, quote=True)

    treatments = {'retain': ('Keep', '残す'), 'shorten': ('Shorten', '短くする'),
                  'possible_omission': ('Remove', '削除する'), 'inspect': ('Review', '確認する')}

    def card(source: dict[str, Any], segment: dict[str, Any], identifier: str, kind: str) -> str:
        target = segment[identifier]
        row = assessments.get((source['source_id'], target), {})
        treatment = row.get('suggested_treatment', 'inspect')
        title = segment.get('label') or ' / '.join(dict.fromkeys(segment.get('observations', [])))
        action = locale_label(locale, *treatments.get(treatment, treatments['inspect']))
        reason = row.get('reason_and_tradeoff') or row.get('potential_contribution') or ''
        instruction = row.get('recommendation') or reason
        recommendation = (instruction if instruction.casefold().startswith(action.casefold() + ' ')
                          else f'{action} — {instruction}') if instruction else action
        context = []
        fields = [('observed_content', 'What happens', '場面の内容'),
                  ('potential_contribution', 'What it adds', 'この場面の役割'),
                  ('evidence_limitation', 'Uncertainty', '不確かな点')]
        if row.get('recommendation'):
            fields.insert(2, ('reason_and_tradeoff', 'Tradeoff', '判断の理由'))
        for key, english, japanese in fields:
            if row.get(key):
                context.append(f'<div><h5>{locale_label(locale, english, japanese)}</h5><p>{prose(row[key])}</p></div>')
        related = [aid for aid in dict.fromkeys(row.get('related_ids', [])) if aid != target and aid in segments]
        if related:
            references = '; '.join(f'{labels[aid]} — {prose(segments[aid].get("label") or " / ".join(segments[aid].get("observations", [])))}' for aid in related)
            context.append(f'<div><h5>{locale_label(locale, "Referenced moments", "参照する場面")}</h5><p>{references}</p></div>')
        if row.get('evidence_ids'):
            excerpts = [r for r in source['speech'] if intersects(r, segment) and r.get('text')]
            cited = [r for r in excerpts if r['evidence_id'] in row.get('evidence_ids', [])]
            context.extend(f'<blockquote>{prose(r["text"])}</blockquote>' for r in cited[:2])
        picture = (f'<img loading="lazy" src="{html.escape(images[target], quote=True)}" alt="{labels[target]} {prose(title)}">'
                   if images and target in images else '')
        return f'<article class="segment {kind} {treatment}"><header><span class="marker">{labels[target]}</span><h3>{prose(title)}</h3></header><div class="panels"><div class="recommendation"><h4>{prose(recommendation)}</h4>{picture}</div><aside>{"".join(context)}</aside></div></article>'

    sections = []
    for source in artifact.get('catalog', []):
        if len(artifact['catalog']) > 1:
            part = (source_parts or {}).get(source['source_id'], '')
            suffix = f' — {prose(part)}' if part else ''
            sections.append(f'<h2 class="recording">{prose(source["name"])}{suffix}</h2>')
        for activity in source['activities']:
            sections.append('<section class="activity-group">' + card(source, activity, 'activity_id', 'activity')
                            + (narration_notes or {}).get(activity['activity_id'], ''))
            sections.append('</section>')
    return ''.join(sections)


def render_recommendation_page(project: dict[str, Any], images: dict[str, str], locale: str) -> str:
    parts = project.get('outputs', {}).get('exo_parts', [])
    source_parts = {sid: locale_label(locale, f'EXO part {i}', f'EXO {i}')
                    for i, part in enumerate(parts, 1) for sid in part['source_ids']} if len(parts) > 1 else {}
    labels = marker_labels(project['editorial_map']['editor_recommendations'])
    references = _reference_labels(project['editorial_map']['editor_recommendations'], labels)
    pattern = _identifier_pattern(references)
    def brief_text(value: Any) -> str:
        text = str(value or '')
        return html.escape(pattern.sub(lambda match: references[match[0]], text) if pattern else text)
    narration: dict[str, str] = {}
    for brief in project['editorial_map'].get('narration_briefs', []):
        marker = labels.get(brief.get('first_state_id', ''), labels.get(brief.get('activity_id', ''), ''))
        picture = images.get(brief.get('first_state_id', ''), '')
        image_tag = f'<img loading="lazy" src="{html.escape(picture, quote=True)}" alt="{html.escape(marker)}">' if picture else ''
        activity_id = brief.get('activity_id', '')
        points = ''.join(f'<li>{brief_text(point)}</li>' for point in brief.get('talking_points', []))
        visuals = ''.join(f'<li>{brief_text(point)}</li>' for point in brief.get('representative_visuals', []))
        narration[activity_id] = narration.get(activity_id, '') + f'<article class="segment"><header><span class="marker">{html.escape(marker)}</span><h3>{locale_label(locale, "Narration brief", "ナレーション資料")}</h3></header><div class="panels"><div class="recommendation"><h4>{brief_text(brief.get("purpose", ""))}</h4><p>{brief_text(brief.get("memory_jog", ""))}</p>{image_tag}</div><aside><h5>{locale_label(locale, "Points to cover", "話す内容")}</h5><ul>{points}</ul><h5>{locale_label(locale, "Suggested footage", "素材候補")}</h5><ul>{visuals}</ul></aside></div></article>'
    notes = recommendation_html(project['editorial_map']['editor_recommendations'], images, locale, source_parts, narration)
    title = html.escape(str(project['title_or_game']))
    return f'''<!doctype html><html lang="{locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — Editing notes</title>
<style>
:root{{font:17px/1.55 system-ui,sans-serif;color-scheme:dark;background:#111820;color:#e7edf4}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1360px;margin:auto;padding:28px 32px 80px}}h1{{font-size:1.9rem;margin:0 0 28px}}
.activity-group{{margin:0 0 48px}}.segment{{width:100%;border:1px solid #344250;border-radius:10px;background:#18232e;margin:12px 0;overflow:hidden}}
.segment>header{{display:flex;align-items:baseline;gap:14px;padding:16px 22px;border-bottom:1px solid #344250}}h3{{font-size:1.05rem;margin:0;font-weight:550}}
.marker{{font-size:1.1rem;font-weight:750;color:#87dbd0;white-space:nowrap}}.activity>header{{background:#243643}}.activity .marker{{font-size:1.3rem}}
.panels{{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr)}}.recommendation{{padding:22px}}h4{{font-size:1.4rem;line-height:1.4;font-weight:650;margin:0 0 20px;color:#f4f8fc}}
.recommendation img{{display:block;width:100%;max-height:270px;object-fit:contain;object-position:left center;border-radius:5px}}aside{{padding:22px;border-left:1px solid #344250;font-size:.88rem;color:#adbac8;background:#15202a}}
aside h5{{font-size:.8rem;color:#d0d9e3;margin:0 0 4px;font-weight:650}}aside p{{margin:0 0 16px}}blockquote{{margin:12px 0;padding-left:12px;border-left:2px solid #486373;color:#c2ccd6}}
.state h4{{font-size:1.25rem}}.segment p,.segment h3,.segment h4{{overflow-wrap:anywhere}}.retain .recommendation{{border-left:3px solid #79b6a4}}.shorten .recommendation{{border-left:3px solid #dbc078}}.possible_omission .recommendation{{border-left:3px solid #b597ca}}
@media(max-width:760px){{main{{padding:18px 12px}}.panels{{grid-template-columns:1fr}}aside{{border-left:0;border-top:1px solid #344250}}}}
@media print{{:root{{color-scheme:light;background:white;color:black}}.segment,aside{{background:white;color:black}}h4,aside h5,.marker{{color:black}}.segment{{break-inside:avoid}}}}
</style></head><body><main><h1>{title}</h1>{notes}</main></body></html>'''
