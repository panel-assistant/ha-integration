"""Signed build feed: internal ha-paneld builds published outside GitHub releases.

The feed is one canonical JSON document plus a detached signature made with the
same release key that signs GitHub release metadata. Trust therefore comes from
the signature, never from where the feed is hosted: the host only has to serve
bytes, and every APK must match the size and SHA-256 the signed feed names.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from aiohttp import ClientSession
from yarl import URL

from .app_identity import (
    BUILD_FEED_SCHEMAS,
    INSTALL_DESCRIPTOR_SCHEMAS,
    LEGACY_PACKAGE_ID,
    is_accepted_package_id,
    is_launch_component,
)
from .release import (
    _DATABASE_COMPATIBILITY_PATTERN,
    _MAX_ANDROID_SDK,
    _MAX_ANDROID_VERSION_CODE,
    _MAX_APK_BYTES,
    _MAX_PROTOCOL_METADATA_BYTES,
    _RELEASE_SIGNER_CERTIFICATE_SHA256,
    _REPOSITORY_RELEASE_ROOT,
    _SHA256_PATTERN,
    _SUPPORTED_ABIS,
    _VERSION_NAME_PATTERN,
    InstallDescriptor,
    ReleaseArtifact,
    ReleaseResolutionError,
    _async_fetch_bounded,
    _bounded_integer,
    _is_trusted_download_url,
    _object_without_duplicates,
    _reject_json_constant,
    _verify_detached_signature,
    feed_build_tag,
    is_install_release_tag,
    parse_protocol_metadata,
    release_apk_name,
)

FEED_CHANNELS = frozenset({"maintainer", "beta"})
_MAX_FEED_BYTES = 256 * 1024
_MAX_SIGNATURE_BYTES = 512
_MAX_FEED_BUILDS = 500
_FEED_FIELDS = frozenset({"builds", "channel", "schema"})
_BUILD_FIELDS = frozenset(
    {
        "apkPath",
        "apkSha256",
        "apkSize",
        "commit",
        "databaseCompatibility",
        "launchComponent",
        "minSdk",
        "packageId",
        "published",
        "signerCertificateSha256",
        "supportedAbis",
        "versionCode",
        "versionName",
    }
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PUBLISHED_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_FEED_HEADERS = {"Accept": "application/json", "Cache-Control": "no-cache"}
_APK_HEADERS = {"Accept": "application/vnd.android.package-archive"}
_APK_DOWNLOAD_SECONDS = 5 * 60
# A slow link may pause between chunks of a large file; metadata reads keep 5 s.
_APK_READ_SECONDS = 30.0


class BuildFeedError(Exception):
    """Raised when a build feed or one of its APKs cannot be authenticated."""


class BuildDownloadError(BuildFeedError):
    """Raised when an APK could not be fetched, so nothing was checked against it."""


@dataclass(frozen=True, slots=True)
class FeedBuild:
    """One signed internal build: its identity and where its exact bytes live."""

    version_code: int
    version_name: str
    apk_url: URL
    apk_sha256: str
    apk_size: int
    commit: str
    database_compatibility: str
    min_sdk: int
    published: str
    package_id: str
    launch_component: str
    protocol_min: int | None = None
    protocol_max: int | None = None

    @property
    def label(self) -> str:
        """Name the build the way the update entity shows it."""
        return build_label(self.version_name, self.version_code)


@dataclass(frozen=True, slots=True)
class BuildFeed:
    """An authenticated feed, newest build first.

    The exact signed bytes are kept so a browser can verify them independently.
    """

    channel: str
    builds: tuple[FeedBuild, ...]
    raw: bytes = field(default=b"", repr=False)
    signature: bytes = field(default=b"", repr=False)

    def newest(self, package_id: str | None = LEGACY_PACKAGE_ID) -> FeedBuild | None:
        """Return the build with the highest version code."""
        return next((b for b in self.builds if b.package_id == package_id), None)

    def find(
        self, version_code: int, package_id: str | None = LEGACY_PACKAGE_ID
    ) -> FeedBuild | None:
        """Return the build with exactly this version code."""
        return next(
            (
                b
                for b in self.builds
                if b.version_code == version_code and b.package_id == package_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True, repr=False)
class FeedInstallBundle:
    """One feed build plus the exact signed feed, for independent verification."""

    artifact: ReleaseArtifact
    feed: bytes
    feed_signature: bytes


def feed_release_artifact(build: FeedBuild) -> ReleaseArtifact:
    """Present a signed feed build to the install paths as a release artifact.

    The descriptor carries the same facts a GitHub release descriptor does; the
    tag and file name are the feed build's own identity.
    """
    tag = feed_build_tag(build.version_code, build.package_id)
    apk_name = f"{build.apk_sha256}.apk"
    return ReleaseArtifact(
        tag=tag,
        version=build.version_name,
        apk_name=apk_name,
        apk_url=str(build.apk_url),
        sha256=build.apk_sha256,
        protocol_min=build.protocol_min,
        protocol_max=build.protocol_max,
        descriptor=InstallDescriptor(
            schema=INSTALL_DESCRIPTOR_SCHEMAS[0],
            release_tag=tag,
            version_name=build.version_name,
            version_code=build.version_code,
            apk_name=apk_name,
            apk_size=build.apk_size,
            apk_sha256=build.apk_sha256,
            package_id=build.package_id,
            signer_certificate_sha256=_RELEASE_SIGNER_CERTIFICATE_SHA256,
            min_sdk=build.min_sdk,
            supported_abis=_SUPPORTED_ABIS,
            database_compatibility=build.database_compatibility,
            launch_component=build.launch_component,
        ),
    )


def build_label(version_name: str, version_code: int) -> str:
    """Show a build by name and number; internal builds share one name."""
    return f"{version_name} build {version_code}"


_LABEL_PATTERN = re.compile(r"^(?:.* build )?([1-9][0-9]{0,9})$")


def parse_build_request(value: str) -> int | None:
    """Read the build number from "772" or "0.9.7-rc4 build 772"."""
    match = _LABEL_PATTERN.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        return None
    code = int(match.group(1))
    return code if code <= _MAX_ANDROID_VERSION_CODE else None


def normalize_feed_url(value: object) -> URL:
    """Accept only a plain HTTPS URL to a JSON document."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(not "!" <= character <= "~" for character in value)
    ):
        raise BuildFeedError
    try:
        url = URL(value)
    except (TypeError, ValueError) as err:
        raise BuildFeedError from err
    if (
        url.scheme != "https"
        or not url.host
        or url.user is not None
        or url.password is not None
        or url.query_string
        or url.fragment
        or not url.path.endswith(".json")
    ):
        raise BuildFeedError
    return url


def _canonical(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _database_range_valid(value: object) -> bool:
    """The same database range rule a release descriptor must meet."""
    match = (
        _DATABASE_COMPATIBILITY_PATTERN.fullmatch(value)
        if isinstance(value, str)
        else None
    )
    if match is None or any(len(group) > 10 for group in match.groups()):
        return False
    low, high = int(match.group(1)), int(match.group(2))
    return 1 <= low <= high <= _MAX_ANDROID_VERSION_CODE


def _parse_build(entry: Any, feed_url: URL) -> FeedBuild | None:
    if not isinstance(entry, dict) or entry.keys() != _BUILD_FIELDS:
        raise BuildFeedError
    sha256 = entry["apkSha256"]
    version_name = entry["versionName"]
    commit = entry["commit"]
    published = entry["published"]
    compatibility = entry["databaseCompatibility"]
    try:
        version_code = _bounded_integer(
            entry["versionCode"], 1, _MAX_ANDROID_VERSION_CODE
        )
        apk_size = _bounded_integer(entry["apkSize"], 1, _MAX_APK_BYTES)
        min_sdk = _bounded_integer(entry["minSdk"], 1, _MAX_ANDROID_SDK)
    except ReleaseResolutionError as err:
        raise BuildFeedError from err
    if (
        not isinstance(sha256, str)
        or _SHA256_PATTERN.fullmatch(sha256) is None
        or entry["apkPath"] != f"apks/{sha256}.apk"
        or not isinstance(version_name, str)
        or _VERSION_NAME_PATTERN.fullmatch(version_name) is None
        or not isinstance(commit, str)
        or _COMMIT_PATTERN.fullmatch(commit) is None
        or not isinstance(published, str)
        or _PUBLISHED_PATTERN.fullmatch(published) is None
        or not _database_range_valid(compatibility)
        or not isinstance(entry["packageId"], str)
        or not isinstance(entry["launchComponent"], str)
        or entry["signerCertificateSha256"] != _RELEASE_SIGNER_CERTIFICATE_SHA256
        or entry["supportedAbis"] != list(_SUPPORTED_ABIS)
    ):
        raise BuildFeedError
    # Authentic metadata can name an app this reader cannot drive.
    if not is_accepted_package_id(entry["packageId"]) or not is_launch_component(
        entry["packageId"], entry["launchComponent"]
    ):
        return None
    return FeedBuild(
        version_code=version_code,
        version_name=version_name,
        apk_url=feed_url.join(URL(entry["apkPath"])),
        apk_sha256=sha256,
        apk_size=apk_size,
        commit=commit,
        database_compatibility=compatibility,
        min_sdk=min_sdk,
        published=published,
        package_id=entry["packageId"],
        launch_component=entry["launchComponent"],
    )


def parse_build_feed(body: bytes, signature: bytes, feed_url: URL) -> BuildFeed:
    """Authenticate the exact feed bytes, then parse its closed canonical shape."""
    if len(body) > _MAX_FEED_BYTES:
        raise BuildFeedError
    try:
        _verify_detached_signature(body, signature)
    except ReleaseResolutionError as err:
        raise BuildFeedError from err
    try:
        document: Any = json.loads(
            body.decode("ascii"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (
        ReleaseResolutionError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ) as err:
        raise BuildFeedError from err
    if (
        not isinstance(document, dict)
        or document.keys() != _FEED_FIELDS
        or document["schema"] not in BUILD_FEED_SCHEMAS
        or not isinstance(document["channel"], str)
        or document["channel"] not in FEED_CHANNELS
        or not isinstance(document["builds"], list)
        or len(document["builds"]) > _MAX_FEED_BUILDS
    ):
        raise BuildFeedError
    try:
        canonical = _canonical(document)
    except (TypeError, ValueError, UnicodeEncodeError) as err:
        raise BuildFeedError from err
    if body != canonical:
        raise BuildFeedError
    builds = [
        build
        for entry in document["builds"]
        if (build := _parse_build(entry, feed_url)) is not None
    ]
    if len({(build.version_code, build.package_id) for build in builds}) != len(builds):
        raise BuildFeedError
    return BuildFeed(
        channel=document["channel"],
        builds=tuple(sorted(builds, key=lambda b: b.version_code, reverse=True)),
        raw=body,
        signature=signature,
    )


def bind_build_feed_protocol(
    feed: BuildFeed, body: bytes, signature: bytes
) -> BuildFeed:
    """Attach authenticated ranges only to exact hashes retained in the feed.

    Older retained builds may lack proof; they remain unknown for admission to
    refuse, without inventing historical compatibility or changing signed V1.
    """
    try:
        ranges = parse_protocol_metadata(body, signature)
    except ReleaseResolutionError as err:
        raise BuildFeedError from err
    return replace(
        feed,
        builds=tuple(
            replace(
                build,
                protocol_min=ranges[build.apk_sha256][0],
                protocol_max=ranges[build.apk_sha256][1],
            )
            if build.apk_sha256 in ranges
            else replace(build, protocol_min=None, protocol_max=None)
            for build in feed.builds
        ),
    )


async def async_fetch_build_feed(session: ClientSession, feed_url: URL) -> BuildFeed:
    """Fetch V1 feed and signed protocol companion from configured siblings."""
    signature_url = feed_url.with_name(f"{feed_url.name}.sig")
    protocol_url = feed_url.with_name(
        f"{feed_url.name.removesuffix('.json')}.protocol.json"
    )
    protocol_signature_url = protocol_url.with_name(f"{protocol_url.name}.sig")
    try:
        body = await _async_fetch_bounded(
            session,
            feed_url,
            _MAX_FEED_BYTES,
            allow_release_redirects=False,
            headers=_FEED_HEADERS,
        )
        signature = await _async_fetch_bounded(
            session,
            signature_url,
            _MAX_SIGNATURE_BYTES,
            allow_release_redirects=False,
            headers=_FEED_HEADERS,
        )
    except ReleaseResolutionError as err:
        raise BuildFeedError from err
    feed = parse_build_feed(body, signature, feed_url)
    try:
        protocol_body = await _async_fetch_bounded(
            session,
            protocol_url,
            _MAX_PROTOCOL_METADATA_BYTES,
            allow_release_redirects=False,
            headers=_FEED_HEADERS,
        )
        protocol_signature = await _async_fetch_bounded(
            session,
            protocol_signature_url,
            _MAX_SIGNATURE_BYTES,
            allow_release_redirects=False,
            headers=_FEED_HEADERS,
        )
    except ReleaseResolutionError as err:
        raise BuildFeedError from err
    return bind_build_feed_protocol(feed, protocol_body, protocol_signature)


async def async_download_build(
    session: ClientSession, artifact: ReleaseArtifact
) -> bytes:
    """Download one signed APK and prove it is exactly the signed size and hash.

    A feed build comes only from its own URL. A GitHub release asset may
    redirect, but only between GitHub's own asset hosts.
    """
    descriptor = artifact.descriptor
    url = URL(artifact.apk_url)
    if descriptor is None:
        # Only the canonical old-id asset resolved from a signed release checksum
        # has no install descriptor. The release's descriptor names the successor.
        if (
            not is_install_release_tag(artifact.tag)
            or artifact.apk_name != release_apk_name(artifact.tag, LEGACY_PACKAGE_ID)
            or artifact.apk_url
            != f"{_REPOSITORY_RELEASE_ROOT}/{artifact.tag}/{artifact.apk_name}"
            or _SHA256_PATTERN.fullmatch(artifact.sha256) is None
        ):
            raise BuildFeedError
        maximum_bytes = _MAX_APK_BYTES
    else:
        if artifact.sha256 != descriptor.apk_sha256:
            raise BuildFeedError
        maximum_bytes = descriptor.apk_size
    try:
        body = await _async_fetch_bounded(
            session,
            url,
            maximum_bytes,
            allow_release_redirects=_is_trusted_download_url(url),
            headers=_APK_HEADERS,
            total_seconds=_APK_DOWNLOAD_SECONDS,
            read_seconds=_APK_READ_SECONDS,
        )
    except ReleaseResolutionError as err:
        raise BuildDownloadError from err
    # The fetch already refused anything longer than the signed size, and a
    # shorter body cannot carry the signed hash.
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise BuildFeedError
    return body
