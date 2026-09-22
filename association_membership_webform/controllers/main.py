import hmac
import json
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..models.webform_intake import WebformError

_logger = logging.getLogger(__name__)

TOKEN_PARAMETER = "association_membership_webform.token"
TOKEN_HEADER = "X-Webform-Token"
USER_PARAMETER = "association_membership_webform.user_id"

# A signup is a few kilobytes. The cap is generous, but it stops an oversized
# body from being read into memory and written into unbounded text columns.
MAX_BODY_BYTES = 256 * 1024


def _as_bytes(value):
    """Bytes for a constant-time comparison.

    ``hmac.compare_digest`` refuses to compare ``str`` with a character above
    U+007F, and WSGI decodes headers as latin-1 — so a single high byte in the
    token header would raise TypeError out of the authentication check and turn
    an unauthenticated request into a 500. Comparing bytes has no such case.
    """
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("utf-8", "surrogateescape")


class MembershipWebformController(http.Controller):
    """One endpoint, receiving signups from the WordPress form.

    ``type="http"`` rather than ``type="json"`` so the endpoint answers with real
    HTTP status codes instead of a JSON-RPC envelope: WordPress stores the status
    and body on the Formidable entry, and that record of the outcome is what makes
    it safe to write straight to the live records without a staging model.
    """

    @http.route(
        "/membership/webform/submit",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def submit(self, **kwargs):
        if not self._authenticated():
            return self._respond(401, {"error": "unauthorized"})

        if (request.httprequest.content_length or 0) > MAX_BODY_BYTES:
            return self._respond(413, {"error": "payload_too_large"})
        try:
            raw = request.httprequest.get_data(as_text=True) or ""
        except Exception:  # noqa: BLE001 - a truncated or undecodable body
            return self._respond(400, {"error": "invalid_payload"})
        # A chunked request carries no Content-Length, so the cap is applied
        # again to what actually arrived.
        if len(raw.encode("utf-8", "surrogateescape")) > MAX_BODY_BYTES:
            return self._respond(413, {"error": "payload_too_large"})
        try:
            payload = json.loads(raw) if raw.strip() else None
        except ValueError:
            return self._respond(400, {"error": "invalid_json"})
        if not isinstance(payload, dict):
            return self._respond(400, {"error": "invalid_payload"})

        # Truncated, and logged with %r: the reference is submitted data, and an
        # embedded newline would otherwise forge a line in the server log.
        reference = str(payload.get("entry_id") or "")[:64]
        if not self._become_intake_user():
            return self._respond(500, {"error": "intake_user_misconfigured"})

        try:
            # One savepoint around the whole mapping: a failure half way through
            # must not leave a partner behind without its membership.
            with request.env.cr.savepoint():
                result = request.env["membership.webform.intake"].sudo().process(payload)
        except WebformError as error:
            _logger.info(
                "Webform entry %r rejected (%s): %s", reference, error.code, error
            )
            return self._respond(422, {"error": error.code, "message": str(error)})
        except (ValidationError, UserError, AccessError) as error:
            # A constraint or a configuration problem, not a bug — typically
            # membership data that is not set up yet for this association. Report
            # it so WordPress records something the operator can act on instead of
            # an opaque 500. The message is Odoo's own text, so it may name
            # records: it is returned only to the authenticated caller, and it is
            # stored wherever that caller records responses.
            _logger.warning(
                "Webform entry %r refused by Odoo: %s", reference, error, exc_info=True
            )
            return self._respond(
                422, {"error": "rejected_by_odoo", "message": str(error)}
            )
        except Exception:  # noqa: BLE001 - the endpoint must never leak a traceback
            _logger.exception("Webform entry %r failed", reference)
            return self._respond(500, {"error": "internal_error"})

        _logger.info(
            "Webform entry %r accepted: partner=%s membership=%s warnings=%s",
            reference,
            result["partner_id"],
            result["membership_id"],
            result["warnings"],
        )
        return self._respond(200, result)

    def _become_intake_user(self):
        """Rebind the request to a real internal user before doing any work.

        ``sudo()`` only flips the superuser flag; it keeps the uid, which on an
        ``auth="public"`` route is the Public user. That is not enough, because
        Odoo deliberately runs computed fields declared without ``compute_sudo``
        as the *real* user (``Field.compute_value`` calls ``records.sudo(False)``).
        With OCA ``base_multi_company`` installed, ``res.partner.company_id`` is
        exactly such a field, so writing a partner as Public raises an AccessError
        from inside the compute no matter how much we sudo around it.

        The user is configurable through ``association_membership_webform.user_id``
        so an operator can give the endpoint a dedicated, auditable account rather
        than the administrator.
        """
        Users = request.env["res.users"].sudo()
        configured = (
            request.env["ir.config_parameter"].sudo().get_param(USER_PARAMETER) or ""
        ).strip()

        user = Users.browse()
        if configured:
            if not configured.isdigit():
                _logger.error("%s must be a user id, got %r", USER_PARAMETER, configured)
                return False
            user = Users.browse(int(configured)).exists()
            if not user:
                _logger.error("%s points at user %s, which does not exist", USER_PARAMETER, configured)
                return False
        else:
            user = request.env.ref("base.user_admin", raise_if_not_found=False) or Users
            user = user.sudo()

        if not user or not user.active or user.share:
            _logger.error(
                "The webform intake user (%s) must be an active internal user",
                user.login if user else "none",
            )
            return False

        request.update_env(user=user.id)
        return True

    def _authenticated(self):
        """Constant-time comparison against the configured shared secret.

        An unset or empty parameter refuses every request, so the endpoint stays
        closed until the token is deliberately configured.
        """
        expected = (
            request.env["ir.config_parameter"].sudo().get_param(TOKEN_PARAMETER) or ""
        )
        if not expected:
            _logger.warning(
                "Webform submission refused: %s is not set", TOKEN_PARAMETER
            )
            return False
        presented = request.httprequest.headers.get(TOKEN_HEADER) or ""
        if hmac.compare_digest(_as_bytes(presented), _as_bytes(expected)):
            return True
        # Every rejection is logged with its source, so repeated attempts are
        # visible and a rate limiter at the reverse proxy has something to act on.
        # Behind a proxy this is the proxy's address unless Odoo runs in
        # proxy mode (``--proxy-mode``) and the proxy sets X-Forwarded-For.
        _logger.warning(
            "Webform submission refused: bad token from %s",
            request.httprequest.remote_addr,
        )
        return False

    def _respond(self, status, body):
        return request.make_json_response(body, status=status)
