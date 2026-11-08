"""Browser display projection; retained evidence and collection bytes stay private."""
import hashlib
import json

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
    result = dict(schemaVersion=SCHEMA,
        **{k: value[k] for k in ('jobId','executionId','clientRequestId','status')},
        score={k: value['score'][k] for k in ('overallScore','validity','reason')},
        visual=dict(score=score,scoreKind=visual['scoreKind'],
            scoreScope=('UNAVAILABLE' if score is None else
                        'ASSESSED_PHONES_ONLY' if partial else 'WHOLE_CLIP')),
        coaching=dict(feedback=value['coaching']['feedback']),failure=value['failure'])
    payload = (json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True,
                          separators=(',',':'))+'\n').encode()
    return payload, hashlib.sha256(payload).hexdigest()
