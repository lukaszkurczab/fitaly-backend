from app.schemas.version import VersionResponse


def build_version_response(version: str, commit_sha: str = "") -> VersionResponse:
    normalized_commit_sha = commit_sha.strip()
    return VersionResponse(
        version=version,
        commitSha=normalized_commit_sha or None,
    )
