"""Read only the designated non-user readiness object with a bounded size."""
import hashlib
import hmac
from .adapters.s3_object_store import create_s3_client_from_environment


def storage_ready(environment, settings):
    client = create_s3_client_from_environment(environment)
    stream = None
    try:
        args = dict(Bucket=environment["AI_ANALYSIS_OBJECT_STORAGE_BUCKET"], Key=settings.readiness_key)
        head = client.head_object(**args)
        if head.get("ContentLength") != settings.readiness_size or not head.get("ETag"):
            return False
        args["IfMatch"] = head["ETag"]
        if head.get("VersionId"):
            args["VersionId"] = head["VersionId"]
        response = client.get_object(**args)
        stream = response["Body"]
        raw = stream.read(settings.readiness_size + 1)
        return (len(raw) == settings.readiness_size and response.get("ETag") == head["ETag"]
                and response.get("ContentLength") == settings.readiness_size
                and hmac.compare_digest(hashlib.sha256(raw).hexdigest(), settings.readiness_sha))
    finally:
        if stream is not None:
            stream.close()
        close = getattr(client, "close", None)
        if close:
            close()
