import base64
import binascii
import hashlib
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .ipp_codec import Attribute as A, Group as G, IncompleteMessage, decode, encode

SIDES = {"one-sided": "simplex", "two-sided-long-edge": "duplexlong", "two-sided-short-edge": "duplexshort"}
OPERATIONS = [2, 4, 8, 9, 10, 11]
TERMINAL = {"submitted", "simulated", "failed", "uncertain", "canceled"}


class IPPError(Exception):
    def __init__(self, code, message, unsupported=None):
        self.code, self.message = code, message
        self.unsupported = unsupported or []


def attr(name, tag, *values):
    return A(name, tag, list(values))


def flatten(groups):
    result = {}
    for group in groups:
        if group.tag not in (1, 2):
            raise IPPError(0x0400, "Invalid attribute group")
        for item in group.attributes:
            if item.name in result:
                raise IPPError(0x0400, "Duplicate attribute")
            result[item.name] = item
    return result


def scalar(attrs, name, tag, default=None):
    item = attrs.get(name)
    if item is None:
        return default
    if item.tag != tag or len(item.values) != 1:
        raise IPPError(0x040B, "Unsupported attribute", [item])
    return item.values[0]


def media_collection():
    return [attr("media-size", 0x34, [attr("x-dimension", 0x21, 21000), attr("y-dimension", 0x21, 29700)]),
            attr("media-size-name", 0x44, "iso_a4_210x297mm")]


def options(attrs, groups):
    copies = scalar(attrs, "copies", 0x21, 1)
    sides = scalar(attrs, "sides", 0x44, "one-sided")
    rejected = []
    for name, valid in (("copies", 1 <= copies <= 99), ("sides", sides in SIDES)):
        if not valid:
            rejected.append(attrs[name])
    allowed = {"media": (0x44, {"iso_a4_210x297mm"}), "number-up": (0x21, {1}),
               "print-color-mode": (0x44, {"monochrome", "auto"}), "print-scaling": (0x44, {"fit", "auto"}),
               "orientation-requested": (0x23, {3}), "finishings": (0x23, {3}),
               "job-sheets": (0x44, {"none"}), "multiple-document-handling": (0x44, {"separate-documents-uncollated-copies"}),
               "printer-resolution": (0x32, {(600, 600, 3)})}
    for name, (tag, values) in allowed.items():
        if name in attrs and scalar(attrs, name, tag) not in values:
            rejected.append(attrs[name])
    if "media-col" in attrs:
        collection = scalar(attrs, "media-col", 0x34)
        members = {a.name: a for a in collection}
        if len(members) != len(collection) or set(members) - {"media-size", "media-size-name"}:
            rejected.append(attrs['media-col'])
        else:
            size = scalar(members, "media-size", 0x34, [])
            dims = {a.name: a for a in size}
            if (size and (set(dims) != {"x-dimension", "y-dimension"} or
                          scalar(dims, "x-dimension", 0x21) != 21000 or scalar(dims, "y-dimension", 0x21) != 29700)) or \
                    scalar(members, "media-size-name", 0x44, "iso_a4_210x297mm") != "iso_a4_210x297mm":
                rejected.append(attrs['media-col'])
    recognized = set(allowed) | {"copies", "sides", "media-col", "job-name"}
    ignored = [a for g in groups if g.tag == 2 for a in g.attributes if a.name not in recognized]
    # Never silently print all pages when the client asked for a page subset.
    if "page-ranges" in attrs:
        rejected.append(attrs['page-ranges'])
    if rejected or (ignored and scalar(attrs, "ipp-attribute-fidelity", 0x22, False)):
        raise IPPError(0x040B, "Unsupported print options", rejected or ignored)
    return copies, SIDES.get(sides, "simplex"), ignored


def selected(attributes, requested, required=()):
    if not requested or "all" in requested:
        return attributes
    groups = {"printer-description", "job-template", "job-description"}
    if groups.intersection(requested):
        return attributes
    return [a for a in attributes if a.name in requested or a.name in required]


def ipp_router(settings, store, access, queue):
    router = APIRouter()
    started = time.time()

    def printer_attributes(uri):
        return [attr("printer-uri-supported", 0x45, uri), attr("uri-authentication-supported", 0x44, "basic"),
                attr("printer-more-info", 0x45, uri.replace('ipp://', 'http://').removesuffix('/ipp/print') + '/'),
                attr("uri-security-supported", 0x44, "none"), attr("printer-name", 0x42, "PrintBridge"),
                attr("printer-info", 0x41, "TOSHIBA PDF Print Bridge"), attr("printer-location", 0x41, "Windows print bridge"),
                attr("printer-make-and-model", 0x41, "PrintBridge PDF"), attr("printer-state", 0x23, 3),
                attr("printer-state-reasons", 0x44, "none"), attr("printer-is-accepting-jobs", 0x22, True),
                attr("printer-state-message", 0x41, "Simulation mode: no paper output" if settings.mode == "simulate" else "Bridge ready; device status not monitored"),
                attr("printer-up-time", 0x21, max(1, int(time.time() - started))),
                attr("queued-job-count", 0x21, sum(j['status'] in {'queued', 'processing'} for j in store.list())),
                attr("ipp-versions-supported", 0x44, "1.1", "2.0"), attr("operations-supported", 0x23, *OPERATIONS),
                attr("charset-configured", 0x47, "utf-8"), attr("charset-supported", 0x47, "utf-8"),
                attr("natural-language-configured", 0x48, "en"), attr("generated-natural-language-supported", 0x48, "en"),
                attr("document-format-default", 0x49, "application/pdf"), attr("document-format-supported", 0x49, "application/pdf", "application/octet-stream"),
                attr("compression-supported", 0x44, "none"), attr("job-k-octets-supported", 0x33, (0, settings.max_upload_mb * 1024)),
                attr("copies-default", 0x21, 1), attr("copies-supported", 0x33, (1, 99)),
                attr("sides-default", 0x44, "one-sided"), attr("sides-supported", 0x44, *SIDES),
                attr("media-default", 0x44, "iso_a4_210x297mm"), attr("media-supported", 0x44, "iso_a4_210x297mm"),
                attr("media-ready", 0x44, "iso_a4_210x297mm"), attr("media-col-default", 0x34, media_collection()),
                attr("media-col-database", 0x34, media_collection()), attr("media-col-ready", 0x34, media_collection()),
                attr("media-col-supported", 0x44, "media-size", "media-size-name"),
                attr("media-size-supported", 0x34, [attr("x-dimension", 0x21, 21000), attr("y-dimension", 0x21, 29700)]),
                attr("color-supported", 0x22, False), attr("print-color-mode-default", 0x44, "monochrome"),
                attr("print-color-mode-supported", 0x44, "monochrome"), attr("print-scaling-default", 0x44, "fit"),
                attr("print-scaling-supported", 0x44, "fit"), attr("number-up-default", 0x21, 1), attr("number-up-supported", 0x21, 1),
                attr("printer-resolution-default", 0x32, (600, 600, 3)), attr("printer-resolution-supported", 0x32, (600, 600, 3)),
                attr("multiple-document-jobs-supported", 0x22, False), attr("multiple-document-handling-default", 0x44, "separate-documents-uncollated-copies"),
                attr("multiple-document-handling-supported", 0x44, "separate-documents-uncollated-copies"),
                attr("pdl-override-supported", 0x44, "attempted"), attr("job-creation-attributes-supported", 0x44, "job-name", "copies", "sides", "media", "media-col"),
                attr("which-jobs-supported", 0x44, "completed", "not-completed", "all")]

    def job_attributes(job, uri):
        state, reason = {"queued": (3, "none"), "processing": (5, "job-transforming"),
                         "submitted": (9, "queued-in-device"), "simulated": (9, "job-completed-successfully"),
                         "failed": (8, "aborted-by-system"), "uncertain": (8, "aborted-by-system"),
                         "canceled": (7, "job-canceled-by-user")}[job['status']]
        message = {"submitted": "Forwarded to Windows; paper output unconfirmed", "simulated": "Simulation only: nothing printed",
                   "uncertain": "Outcome unknown; check Windows queue before retrying"}.get(job['status'], job['status'])
        return [attr("job-id", 0x21, job['number']), attr("job-uri", 0x45, f"{uri}/jobs/{job['number']}"),
                attr("job-printer-uri", 0x45, uri), attr("job-name", 0x42, job['name']),
                attr("job-originating-user-name", 0x42, "printbridge"), attr("job-state", 0x23, state),
                attr("job-state-reasons", 0x44, reason), attr("job-state-message", 0x41, message),
                attr("copies", 0x21, job['copies']), attr("sides", 0x44, next(k for k, v in SIDES.items() if v == job['sides'])),
                attr("time-at-creation", 0x21, max(0, int(job['created_at'] - started))),
                attr("job-printer-up-time", 0x21, max(1, int(time.time() - started)))]

    async def handle(request: Request):
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/ipp':
            return Response(status_code=415)
        stream = request.stream().__aiter__()
        header = bytearray()
        message = None
        tail = b''
        try:
            # Decode only bounded attribute bytes; pass document chunks to the shared queue.
            while message is None:
                chunk = await anext(stream, None)
                if chunk is None:
                    raise ValueError()
                room = 65536 - len(header)
                header.extend(chunk[:room])
                try:
                    message = decode(bytes(header))
                    tail = bytes(header[message.document_offset:]) + chunk[room:]
                except IncompleteMessage:
                    if len(header) >= 65536:
                        raise ValueError()
        except ValueError:
            return Response(status_code=400)

        def reply(code=0, groups=None, text=None):
            operation = [attr("attributes-charset", 0x47, "utf-8"), attr("attributes-natural-language", 0x48, "en")]
            if text:
                operation.append(attr("status-message", 0x41, text))
            version = message.version if message.version in {(1, 1), (2, 0)} else (1, 1)
            return Response(encode(version, code, message.request_id, [G(1, operation)] + (groups or [])), media_type='application/ipp')

        try:
            if message.version not in {(1, 1), (2, 0)}:
                raise IPPError(0x0503, "IPP version not supported")
            if message.request_id <= 0 or not message.groups or message.groups[0].tag != 1:
                raise IPPError(0x0400, "Invalid IPP request")
            attrs = flatten(message.groups)
            if scalar(attrs, 'attributes-charset', 0x47) != 'utf-8':
                raise IPPError(0x040D, "Only UTF-8 is supported")
            scalar(attrs, 'attributes-natural-language', 0x48, 'en')
            uri = 'ipp://' + request.url.netloc + '/ipp/print'
            requested = attrs.get('requested-attributes')
            if requested and requested.tag != 0x44:
                raise IPPError(0x0400, "Invalid requested attributes")
            requested = requested.values if requested else []
            if message.code == 11:
                return reply(groups=[G(4, selected(printer_attributes(uri), requested))])

            authorization = request.headers.get('authorization', '')
            owner = None
            if authorization.startswith('Basic '):
                try:
                    username, secret = base64.b64decode(authorization[6:], validate=True).decode('utf-8').split(':', 1)
                    if username == 'printbridge':
                        owner = access.authenticate(secret)
                except (ValueError, UnicodeError, binascii.Error):
                    pass
            if not owner:
                return Response(status_code=401, headers={'WWW-Authenticate': 'Basic realm="PrintBridge", charset="UTF-8"'})
            if message.code not in OPERATIONS:
                raise IPPError(0x0501, "Operation not supported")
            if message.code in (2, 4):
                fmt = scalar(attrs, 'document-format', 0x49, 'application/pdf')
                if fmt not in {'application/pdf', 'application/octet-stream'}:
                    raise IPPError(0x040A, "Only PDF documents are supported")
                if scalar(attrs, 'compression', 0x44, 'none') != 'none':
                    raise IPPError(0x040F, "Compression not supported")
                copies, sides, ignored = options(attrs, message.groups)
                if message.code == 4:
                    return reply(1 if ignored else 0, [G(5, ignored)] if ignored else None)
                name = scalar(attrs, 'job-name', 0x42, 'IPP document')
                async def document():
                    yield tail
                    async for chunk in stream:
                        yield chunk
                identity = hashlib.sha256(bytes(header[:message.document_offset])).hexdigest()
                peer = request.client.host if request.client else ''
                key = f'ipp:{owner}:{peer}:{identity}'
                try:
                    job = await queue.submit(document(), key, name, copies, sides, owner=owner)
                except HTTPException as exc:
                    raise IPPError({413: 0x0408, 409: 0x0400}.get(exc.status_code, 0x0411), 'PDF rejected or request conflicts') from None
                stored = store.ipp_job(owner, job_id=job['id'])
                return reply(1 if ignored else 0, ([G(5, ignored)] if ignored else []) + [G(2, job_attributes(stored, uri))])
            if message.code == 10:
                which = scalar(attrs, 'which-jobs', 0x44, 'not-completed')
                if which not in {'completed', 'not-completed', 'all'}:
                    raise IPPError(0x040B, 'Unsupported which-jobs', [attrs['which-jobs']])
                limit = scalar(attrs, 'limit', 0x21, 100)
                if limit < 1:
                    raise IPPError(0x0400, 'Invalid limit')
                jobs = [j for j in store.ipp_list(owner) if which == 'all' or (j['status'] in TERMINAL) == (which == 'completed')]
                return reply(groups=[G(2, selected(job_attributes(j, uri), requested, ('job-id', 'job-uri'))) for j in jobs[:min(limit, 50)]])
            number = scalar(attrs, 'job-id', 0x21)
            job_uri = scalar(attrs, 'job-uri', 0x45)
            if job_uri:
                path = urlsplit(job_uri).path
                if not path.startswith('/ipp/print/jobs/') or not path.rsplit('/', 1)[-1].isdigit():
                    raise IPPError(0x0400, 'Invalid job URI')
                uri_number = int(path.rsplit('/', 1)[-1])
                if number is not None and number != uri_number:
                    raise IPPError(0x0400, 'Job target mismatch')
                number = uri_number
            if not isinstance(number, int) or not 1 <= number <= 2147483647:
                raise IPPError(0x0400, 'Invalid job ID')
            job = store.ipp_job(owner, number=number)
            if not job:
                raise IPPError(0x0406, 'Job not found')
            if message.code == 8:
                if not store.cancel_ipp(owner, number):
                    raise IPPError(0x0404, 'Only queued jobs can be canceled; inspect Windows for submitted jobs')
                return reply()
            return reply(groups=[G(2, selected(job_attributes(job, uri), requested))])
        except IPPError as exc:
            return reply(exc.code, [G(5, exc.unsupported)] if exc.unsupported else None, exc.message)

    router.add_api_route('/ipp/print', handle, methods=['POST'])
    router.add_api_route('/ipp/print/jobs/{number}', handle, methods=['POST'])
    return router
