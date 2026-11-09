"""Browser display projection; retained evidence and collection bytes stay private."""
import hashlib
import json
from copy import deepcopy

from .http_models import HttpFailure

SCHEMA = 'voice-coaching.browser-video-summary.v1'


def summarize(raw: bytes):
    value = json.loads(raw)
    if value.get('schemaVersion') != 'voice-coaching.independent-audiovisual-result.v1':
        raise HttpFailure(500, 'RESULT_FORMAT_INVALID')
    visual = value['visual']
    score = visual['score']
    partial = 'PARTIAL_REFERENCE_COVERAGE' in visual['reasonCodes']
    # Whitelist fields: additions to private result/evidence never leak here.
    public_score = {k: value['score'][k] for k in ('overallScore','validity','reason')}
    details = value['score'].get('details')
    if isinstance(details, dict) and details.get('schemaVersion') == 'voice-coaching.score-details.v1':
        # Only final component scores; never expose worksheet/phone geometry.
        names = ('schemaVersion', 'validationStatus', 'aggregationMode', 'audioScore',
                 'visualScore', 'audioWeight', 'visualWeight')
        rows = details.get('criteria')
        fields = ('criterionId', 'label', 'score', 'status', 'reasonCode', 'weight')
        if (all(k in details for k in names) and isinstance(rows, list)
                and len(rows) == 6 and all(isinstance(r, dict) and all(k in r for k in fields) for r in rows)):
            public_score['details'] = {k: deepcopy(details[k]) for k in names}
            public_score['details']['criteria'] = [{k: r[k] for k in fields} for r in rows]
    result = dict(schemaVersion=SCHEMA,
        **{k: value[k] for k in ('jobId','executionId','clientRequestId','status')},
        score=public_score,
        visual=dict(score=score,scoreKind=visual['scoreKind'],
            scoreScope=('UNAVAILABLE' if score is None else
                        'ASSESSED_PHONES_ONLY' if partial else 'WHOLE_CLIP')),
        coaching=dict(feedback=value['coaching']['feedback']),failure=value['failure'])
    payload = (json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True,
                          separators=(',',':'))+'\n').encode()
    return payload, hashlib.sha256(payload).hexdigest()
