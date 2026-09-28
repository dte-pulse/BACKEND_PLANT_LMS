"""PPWEC endpoints — Pulse Professional Workplace Excellence Certification.

Learner experience: module catalog/player flow, assessments, Learning
Passport, points, badges, 7-Day Challenge, certificate. Admin/HR: catalog
authoring, org/function/individual reporting.
"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.ppwec_service import PpwecService
from app.storage.file_storage import FileStorageService, UploadValidationError

router = APIRouter(prefix='/ppwec', tags=['ppwec'])


def get_ppwec_service(db: Session = Depends(get_db)):
    return PpwecService(db)


# ─── Request schemas ─────────────────────────────────────────────────────────

class ScreenCompleteRequest(BaseModel):
    screen_id: int
    interaction_result: dict | None = Field(default=None)
    time_spent_seconds: int = Field(default=0, ge=0)


class AssessmentSubmitRequest(BaseModel):
    attempt_id: int
    answers: dict = Field(default_factory=dict)  # {question_id: selected_key}


class ChallengeTickRequest(BaseModel):
    day: int = Field(ge=1, le=7)
    done: bool = True


class ModuleUpsertRequest(BaseModel):
    module_number: int = Field(ge=1, le=18)
    title: str
    theme: str
    description: str | None = None
    estimated_minutes: int = 60
    passing_score: float = Field(default=80.0, ge=0, le=100)
    badge_name: str | None = None
    badge_icon: str | None = None
    status: str = 'draft'  # draft | reviewed | approved | final (S29 versioning)
    version: str = 'V1.0'
    content_owner: str | None = None
    is_mandatory: bool = True


class ScreenUpsertRequest(BaseModel):
    screen_number: int = Field(ge=1)
    section: str = 'learning'
    title: str
    on_screen_text: dict | list | None = None
    voice_over: str | None = None
    audio_url: str | None = None
    caption_url: str | None = None
    transcript: str | None = None
    video_url: str | None = None
    visual_direction: str | None = None
    interaction_type: str = 'content'
    interaction_payload: dict | None = None
    is_mandatory: bool = True
    estimated_seconds: int = 60
    pulse_anchor: str | None = None
    md_philosophy: bool = False


class QuestionUpsertRequest(BaseModel):
    question_type: str = 'knowledge'
    case_context: str | None = None
    question_text: str
    options: list  # [{key, text}]
    correct_option: str
    feedback_why: str | None = None
    feedback_better: str | None = None
    is_active: bool = True


class MediaAttachRequest(BaseModel):
    """Attach uploaded media (or any reachable URL) to a screen."""
    audio_url: str | None = None
    caption_url: str | None = None
    video_url: str | None = None


class TTSExportRequest(BaseModel):
    """Batch TTS request for narration production (§7/§A: 60-70% coverage)."""
    screen_numbers: list[int] | None = None  # default: all screens with voice_over
    format: str = Field(default='json')  # json | srt


class PilotFeedbackRequest(BaseModel):
    engagement: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    realism: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    comments: str | None = None
    function_tag: str | None = None


class SignoffRequest(BaseModel):
    stage: str  # content_owner | md_leadership | ux | pilot
    decision: str  # approved | changes_requested
    notes: str | None = None


class FreezeRequest(BaseModel):
    frozen: bool = True


class BulkImportRequest(BaseModel):
    """Full-module JSON import: mirrors the export shape (round-trip safe)."""
    module: ModuleUpsertRequest
    screens: list[ScreenUpsertRequest] = Field(default_factory=list)
    questions: list[QuestionUpsertRequest] = Field(default_factory=list)
    replace: bool = False  # true = delete existing screens/questions first


def _handle(exc: ValueError):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ─── Learner: catalog & player ───────────────────────────────────────────────

@router.get('/modules')
def list_modules(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    return service.list_modules()


@router.get('/modules/{module_number}')
def module_detail(
    module_number: int,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    d = service.get_module_detail(module_number)
    if not d:
        raise HTTPException(status_code=404, detail='PPWEC module not found')
    return d


@router.get('/modules/{module_number}/state')
def module_state(
    module_number: int,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Resume/navigation state for the current learner (no-skip map included)."""
    try:
        return service.start_module(current_user.id, module_number)
    except ValueError as e:
        _handle(e)


@router.post('/modules/{module_number}/screens/complete')
def complete_screen(
    module_number: int,
    payload: ScreenCompleteRequest,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.complete_screen(
            current_user.id,
            module_number,
            payload.screen_id,
            payload.interaction_result,
            payload.time_spent_seconds,
        )
    except ValueError as e:
        _handle(e)


# ─── Learner: assessment ─────────────────────────────────────────────────────

@router.post('/modules/{module_number}/assessment/start')
def start_assessment(
    module_number: int,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.start_assessment(current_user.id, module_number)
    except ValueError as e:
        _handle(e)


@router.post('/modules/{module_number}/assessment/submit')
def submit_assessment(
    module_number: int,
    payload: AssessmentSubmitRequest,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.submit_assessment(
            current_user.id, module_number, payload.attempt_id, payload.answers
        )
    except ValueError as e:
        _handle(e)


# ─── Learner: Passport, points, badges (S19-S20) ─────────────────────────────

@router.get('/passport')
def get_passport(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    return service.get_passport(current_user.id)


@router.get('/overview')
def my_overview(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Compact stats for dashboard cards."""
    return service.my_overview(current_user.id)


@router.get('/points')
def my_points(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    from app.models.ppwec import PpwecPointsLedger
    rows = (
        service.db.query(PpwecPointsLedger)
        .filter(PpwecPointsLedger.user_id == current_user.id)
        .order_by(PpwecPointsLedger.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            'activity_type': r.activity_type,
            'points': r.points,
            'module_id': r.module_id,
            'reference_id': r.reference_id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ─── Learner: 7-Day Challenge (S22/SQ) ──────────────────────────────────────

@router.post('/modules/{module_number}/challenge/start')
def start_challenge(
    module_number: int,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.start_challenge(current_user.id, module_number)
    except ValueError as e:
        _handle(e)


@router.post('/modules/{module_number}/challenge/tick')
def tick_challenge(
    module_number: int,
    payload: ChallengeTickRequest,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.tick_challenge_day(current_user.id, module_number, payload.day, payload.done)
    except ValueError as e:
        _handle(e)


# ─── Certificate (S20/S23) ──────────────────────────────────────────────────

@router.get('/certificate')
def certificate_data(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    try:
        return service.certificate_data(current_user.id)
    except ValueError as e:
        _handle(e)


@router.get('/certificate/html', response_class=Response)
def certificate_html(
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Print-ready HTML certificate (browser Save-as-PDF)."""
    try:
        data = service.certificate_data(current_user.id)
    except ValueError as e:
        _handle(e)
    earned = data['certification_earned']
    title = data['certification_title'] if earned else 'Progress Statement'
    body = f"""
<html><head><title>{title}</title>
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; margin: 0; background: #f5f5f0; }}
  .cert {{ max-width: 800px; margin: 40px auto; padding: 60px 50px; background: #fffdf8;
           border: 12px double #0f766e; text-align: center; }}
  .org {{ letter-spacing: 4px; font-size: 13px; color: #0f766e; font-weight: bold; }}
  h1 {{ font-size: 34px; margin: 24px 0 8px; color: #111; }}
  .name {{ font-size: 30px; font-style: italic; margin: 28px 0; color: #0f172a;
           border-bottom: 1px solid #999; display: inline-block; padding: 0 40px 6px; }}
  .meta {{ color: #555; font-size: 14px; line-height: 1.8; }}
  .credential {{ margin-top: 28px; font-size: 18px; color: #b45309; font-weight: bold; }}
  .seal {{ margin-top: 44px; display: flex; justify-content: space-between; color: #555; font-size: 12px; }}
  @media print {{ body {{ background: #fff; }} .cert {{ margin: 0; border-color: #0f766e; }} }}
</style></head><body>
  <div class="cert">
    <div class="org">PULSE PHARMACEUTICALS</div>
    <h1>{title}</h1>
    <p class="meta">This certifies that</p>
    <div class="name">{data['employee_name']}</div>
    <p class="meta">
      {'has successfully completed all ' + str(data['total_modules']) + ' modules of the'
       if earned else 'has completed ' + str(data['modules_completed']) + ' of ' + str(data['total_modules']) + ' modules of the'}<br/>
      <strong>Pulse Professional Workplace Excellence Certification</strong> programme
    </p>
    <p class="meta">Modules completed: <strong>{data['modules_completed']} / {data['total_modules']}</strong>
      &nbsp;·&nbsp; Learning Points: <strong>{data['total_points']}</strong></p>
    <div class="credential">{data['credential'] if earned else '&nbsp;'}</div>
    <div class="seal">
      <span>Employee Code: {data['employee_code']}</span>
      <span>Issued: {data['issued_at'][:10]}</span>
    </div>
  </div>
</body></html>"""
    return Response(content=body, media_type='text/html')


# ─── Media: upload, attach, stream (S7/S12/S23) ───────────────────────────

def _screen_media_guard(module_number: int, screen_number: int,
                        current_user: User, service: PpwecService) -> None:
    """Screens are learner-visible content: no extra access restriction beyond auth,
    but validate the screen exists for early, clear errors."""
    d = service.get_module_detail(module_number)
    if not d or not any(s['screen_number'] == screen_number for s in d['screens']):
        raise HTTPException(status_code=404, detail='Screen not found')


@router.post('/admin/modules/{module_number}/screens/{screen_number}/media',
             status_code=status.HTTP_201_CREATED)
def upload_screen_media(
    module_number: int,
    screen_number: int,
    kind: str = Form(...),  # audio | caption | video
    file: UploadFile = File(...),
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Upload a voice-over / caption / video asset and attach it to a screen.

    Validated against the PPWEC media allowlist + container magic bytes
    (VULN-007 pattern); stored via FileStorageService (local disk, S3 when
    configured). Supports small multipart chunks used by upload clients.
    """
    _screen_media_guard(module_number, screen_number, current_user, service)
    if kind not in ('audio', 'caption', 'video'):
        raise HTTPException(status_code=400, detail='kind must be audio, caption or video')
    try:
        storage = FileStorageService()
        stored_name, media_kind, file_url = storage.save_ppwec_media(file)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from app.models.ppwec import PpwecModule, PpwecScreen
    m = service.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
    s = service.db.query(PpwecScreen).filter(
        PpwecScreen.module_id == m.id,
        PpwecScreen.screen_number == screen_number,
    ).first()
    if kind == 'audio':
        s.audio_url = file_url
    elif kind == 'caption':
        s.caption_url = file_url
    else:
        s.video_url = file_url
    service.db.commit()
    return {
        'module_number': module_number,
        'screen_number': screen_number,
        'kind': kind,
        'media_kind_returned': media_kind,
        'stored_name': stored_name,
        'url': file_url,
    }


@router.post('/admin/modules/{module_number}/screens/{screen_number}/media-url',
             status_code=status.HTTP_200_OK)
def attach_screen_media_url(
    module_number: int,
    screen_number: int,
    payload: MediaAttachRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Attach externally-hosted media URLs (CDN, LMS asset server) to a screen."""
    _screen_media_guard(module_number, screen_number, current_user, service)
    from app.models.ppwec import PpwecModule, PpwecScreen
    m = service.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
    s = service.db.query(PpwecScreen).filter(
        PpwecScreen.module_id == m.id,
        PpwecScreen.screen_number == screen_number,
    ).first()
    if payload.audio_url is not None:
        s.audio_url = payload.audio_url
    if payload.caption_url is not None:
        s.caption_url = payload.caption_url
    if payload.video_url is not None:
        s.video_url = payload.video_url
    service.db.commit()
    return {'module_number': module_number, 'screen_number': screen_number,
            'audio_url': s.audio_url, 'caption_url': s.caption_url, 'video_url': s.video_url}


@router.post('/modules/{module_number}/screens/{screen_number}/media-src')
def create_media_src(
    module_number: int,
    screen_number: int,
    kind: str = 'audio',
    current_user: User = Depends(get_current_user),
):
    """Exchange the JWT for a short-lived signed playback URL.

    <audio>/<video> elements cannot set Authorization headers, and browsers
    issue MANY Range requests while playing/seeking — so a single-use ticket
    would break playback. Instead this issues a stateless, scoped, 10-minute
    HMAC token (python-jose, already used for auth) bound to
    (user, module, screen, kind). The long-lived JWT never appears in a media
    URL (VULN-009 spirit).
    """
    if kind not in ('audio', 'caption', 'video'):
        raise HTTPException(status_code=400, detail='kind must be audio, caption or video')
    from datetime import datetime, timedelta, timezone
    from jose import jwt as jose_jwt
    from app.core.config import settings as app_settings
    from app.core.security import ALGORITHM

    now = datetime.now(timezone.utc)
    token = jose_jwt.encode(
        {
            'sub': str(current_user.id),
            'm': module_number,
            's': screen_number,
            'k': kind,
            'iat': int(now.timestamp()),
            'exp': int((now + timedelta(minutes=10)).timestamp()),
            'jti': secrets.token_urlsafe(8),
        },
        app_settings.secret_key,
        algorithm=ALGORITHM,
    )
    return {
        'src': f'/ppwec/media/{module_number}/{screen_number}/{kind}?pt={token}',
        'kind': kind,
        'expires_in': 600,
    }


@router.get('/media/{module_number}/{screen_number}/{kind}')
def stream_screen_media(
    module_number: int,
    screen_number: int,
    kind: str,
    pt: str,
    request: Request,
    service: PpwecService = Depends(get_ppwec_service),
):
    """Stream a screen's audio/caption/video with a signed playback token.

    The token is verified statelessly (HMAC signature + scope + 10-min expiry)
    and tolerates the many Range requests a media element makes. Auth = token
    instead of JWT because media elements cannot set Authorization headers.
    """
    if kind not in ('audio', 'caption', 'video'):
        raise HTTPException(status_code=400, detail='kind must be audio, caption or video')

    from jose import jwt as jose_jwt, JWTError
    from app.core.config import settings as app_settings
    from app.core.security import ALGORITHM
    try:
        claims = jose_jwt.decode(pt, app_settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired playback token')
    if claims.get('m') != module_number or claims.get('s') != screen_number or claims.get('k') != kind:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Playback token does not match this media')

    d = service.get_module_detail(module_number)
    if not d:
        raise HTTPException(status_code=404, detail='Module not found')
    screen = next((s for s in d['screens'] if s['screen_number'] == screen_number), None)
    if not screen:
        raise HTTPException(status_code=404, detail='Screen not found')

    url_field = {'audio': 'audio_url', 'caption': 'caption_url', 'video': 'video_url'}[kind]
    include_payload = True
    # Re-fetch the screen row to read the raw URL fields.
    from app.models.ppwec import PpwecModule, PpwecScreen
    m = service.db.query(PpwecModule).filter(PpwecModule.module_number == module_number).first()
    s = service.db.query(PpwecScreen).filter(
        PpwecScreen.module_id == m.id,
        PpwecScreen.screen_number == screen_number,
    ).first()
    media_url = getattr(s, url_field, None)
    if not media_url:
        raise HTTPException(status_code=404, detail=f'No {kind} asset attached to this screen')

    # Externally hosted (S3 public URL / CDN): hand back the URL directly.
    if media_url.startswith('http://') or media_url.startswith('https://'):
        return {'url': media_url, 'kind': kind}

    # Locally stored file: stream it with Range support.
    from app.core.config import settings as app_settings
    path = Path(app_settings.upload_dir) / media_url
    if not path.exists():
        raise HTTPException(status_code=404, detail='Media file missing on disk')

    range_header = request.headers.get('range')
    file_size = path.stat().st_size
    content_type = {'audio': 'audio/mpeg', 'caption': 'text/vtt', 'video': 'video/mp4'}[kind]
    content_type_by_ext = {
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4',
        '.webm': 'video/webm', '.mp4': 'video/mp4', '.vtt': 'text/vtt',
    }.get(path.suffix.lower(), content_type)

    if range_header:
        import re
        match = re.match(r'bytes=(\d+)-(\d*)', range_header)
        start = int(match.group(1)) if match else 0
        end = int(match.group(2)) if match and match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1
        with open(path, 'rb') as f:
            f.seek(start)
            data = f.read(length)
        return Response(
            content=data,
            status_code=206,
            media_type=content_type_by_ext,
            headers={
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'private, max-age=3600',
            },
        )

    return FileResponse(
        path,
        media_type=content_type_by_ext,
        headers={'Accept-Ranges': 'bytes', 'Cache-Control': 'private, max-age=3600'},
    )


@router.post('/admin/modules/{module_number}/tts-export')
def tts_export(
    module_number: int,
    payload: TTSExportRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Export narration scripts for TTS/recording production (S7, S V.4).

    Returns per-screen voice_over text with file naming, or an SRT/VTT-ready
    timed script at ~150 wpm when format=srt.
    """
    d = service.get_module_detail(module_number)
    if not d:
        raise HTTPException(status_code=404, detail='PPWEC module not found')
    wanted = set(payload.screen_numbers or [])
    items = []
    for s in d['screens']:
        vo = (s.get('voice_over') or '').strip()
        if not vo or (wanted and s['screen_number'] not in wanted):
            continue
        words = len(vo.split())
        seconds = max(4, round(words / 150 * 60))
        entry = {
            'screen_number': s['screen_number'],
            'title': s['title'],
            'text': vo,
            'words': words,
            'est_seconds': seconds,
            'filename': f"M{module_number:02d}-S{s['screen_number']:02d}.mp3",
        }
        if payload.format == 'srt':
            start = 0
            end = seconds
            def ts(t: int) -> str:
                return f"00:{t // 60:02d}:{t % 60:02d}.000"
            entry['srt'] = f"1\n{ts(start)} --> {ts(end)}\n{vo}\n"
        items.append(entry)
    total_seconds = sum(i['est_seconds'] for i in items)
    return {
        'module_number': module_number,
        'coverage': f"{len(items)}/{d['screen_count']} screens",
        'total_est_seconds': total_seconds,
        'items': items,
    }


# ─── Admin/HR: authoring (S25: content owner controls learning intent) ──────

@router.post('/admin/modules', status_code=status.HTTP_201_CREATED)
def upsert_module(
    payload: ModuleUpsertRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    from app.models.ppwec import PpwecModule
    m = service.db.query(PpwecModule).filter(
        PpwecModule.module_number == payload.module_number
    ).first()
    if not m:
        m = PpwecModule(module_number=payload.module_number, title=payload.title, theme=payload.theme)
        service.db.add(m)
    m.title = payload.title
    m.theme = payload.theme
    m.description = payload.description
    m.estimated_minutes = payload.estimated_minutes
    m.passing_score = payload.passing_score
    m.badge_name = payload.badge_name
    m.badge_icon = payload.badge_icon
    m.status = payload.status
    m.version = payload.version
    m.content_owner = payload.content_owner
    m.is_mandatory = payload.is_mandatory
    service.db.commit()
    return service._module_dict(m)


@router.post('/admin/modules/{module_number}/screens', status_code=status.HTTP_201_CREATED)
def upsert_screen(
    module_number: int,
    payload: ScreenUpsertRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    from app.models.ppwec import PpwecModule, PpwecScreen
    m = service.db.query(PpwecModule).filter(
        PpwecModule.module_number == module_number
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail='PPWEC module not found')
    s = service.db.query(PpwecScreen).filter(
        PpwecScreen.module_id == m.id,
        PpwecScreen.screen_number == payload.screen_number,
    ).first()
    if not s:
        s = PpwecScreen(module_id=m.id, screen_number=payload.screen_number)
        service.db.add(s)
    for field in ('section', 'title', 'on_screen_text', 'voice_over', 'audio_url',
                  'caption_url', 'transcript', 'video_url', 'visual_direction',
                  'interaction_type', 'interaction_payload', 'is_mandatory',
                  'estimated_seconds', 'pulse_anchor', 'md_philosophy'):
        setattr(s, field, getattr(payload, field))
    service.db.commit()
    return {'id': s.id, 'module_id': m.id, 'screen_number': s.screen_number}


@router.post('/admin/modules/{module_number}/questions', status_code=status.HTTP_201_CREATED)
def upsert_question(
    module_number: int,
    payload: QuestionUpsertRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    from app.models.ppwec import PpwecModule, PpwecQuestion
    m = service.db.query(PpwecModule).filter(
        PpwecModule.module_number == module_number
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail='PPWEC module not found')
    q = PpwecQuestion(
        module_id=m.id,
        question_type=payload.question_type,
        case_context=payload.case_context,
        question_text=payload.question_text,
        options=payload.options,
        correct_option=payload.correct_option,
        feedback_why=payload.feedback_why,
        feedback_better=payload.feedback_better,
        is_active=payload.is_active,
    )
    service.db.add(q)
    service.db.commit()
    return {'id': q.id, 'module_id': m.id}


# ─── Admin: content validation, export/import (S25/S27) ─────────────────────

@router.get('/admin/modules/{module_number}/validate')
def validate_module(
    module_number: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Run the §X QA checklist programmatically; persists a validation run."""
    from app.services.ppwec_governance_service import PpwecGovernanceService
    try:
        return PpwecGovernanceService(service.db).validate_module(module_number)
    except ValueError as e:
        _handle(e)


@router.get('/admin/modules/{module_number}/export')
def export_module(
    module_number: int,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Full-module JSON export (screens + questions) for backup/git review."""
    d = service.get_module_detail(module_number)
    if not d:
        raise HTTPException(status_code=404, detail='PPWEC module not found')
    from app.models.ppwec import PpwecQuestion
    questions = (
        service.db.query(PpwecQuestion)
        .filter(PpwecQuestion.module_id == d['id'])
        .order_by(PpwecQuestion.id)
        .all()
    )
    return {
        'module': {
            'module_number': d['module_number'], 'title': d['title'], 'theme': d['theme'],
            'description': d['description'], 'estimated_minutes': d['estimated_minutes'],
            'passing_score': d['passing_score'], 'badge_name': d['badge_name'],
            'badge_icon': d['badge_icon'], 'status': d['status'], 'version': d['version'],
            'content_owner': d['content_owner'], 'is_mandatory': d['is_mandatory'],
        },
        'screens': [
            {
                'screen_number': s['screen_number'], 'section': s['section'], 'title': s['title'],
                'on_screen_text': s.get('on_screen_text'), 'voice_over': s.get('voice_over'),
                'audio_url': s.get('audio_url'), 'caption_url': s.get('caption_url'),
                'transcript': s.get('transcript'), 'video_url': s.get('video_url'),
                'visual_direction': s.get('visual_direction'),
                'interaction_type': s['interaction_type'],
                'interaction_payload': s.get('interaction_payload'),
                'is_mandatory': s['is_mandatory'], 'estimated_seconds': s['estimated_seconds'],
                'pulse_anchor': s['pulse_anchor'], 'md_philosophy': s['md_philosophy'],
            }
            for s in d['screens']
        ],
        'questions': [
            {
                'question_type': q.question_type, 'case_context': q.case_context,
                'question_text': q.question_text, 'options': q.options,
                'correct_option': q.correct_option, 'feedback_why': q.feedback_why,
                'feedback_better': q.feedback_better, 'is_active': q.is_active,
            }
            for q in questions
        ],
    }


@router.post('/admin/modules/import', status_code=status.HTTP_200_OK)
def import_module(
    payload: BulkImportRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Bulk JSON import: upserts the module, screens and questions in one shot.

    Runs the §X validation AFTER import and returns the report so invalid
    content is visible immediately (status stays non-final until green).
    """
    from app.models.ppwec import PpwecModule, PpwecQuestion, PpwecScreen
    from app.services.ppwec_governance_service import PpwecGovernanceService

    # 1. upsert module
    m = service.db.query(PpwecModule).filter(
        PpwecModule.module_number == payload.module.module_number).first()
    if not m:
        m = PpwecModule(module_number=payload.module.module_number,
                        title=payload.module.title, theme=payload.module.theme)
        service.db.add(m)
    for f in ('title', 'theme', 'description', 'estimated_minutes', 'passing_score',
              'badge_name', 'badge_icon', 'status', 'version', 'content_owner', 'is_mandatory'):
        setattr(m, f, getattr(payload.module, f))
    service.db.flush()

    # 2. replace-or-upsert screens
    if payload.replace:
        service.db.query(PpwecScreen).filter(PpwecScreen.module_id == m.id).delete()
        service.db.query(PpwecQuestion).filter(PpwecQuestion.module_id == m.id).delete()
        service.db.flush()
    for spec in payload.screens:
        s = service.db.query(PpwecScreen).filter(
            PpwecScreen.module_id == m.id,
            PpwecScreen.screen_number == spec.screen_number).first()
        if not s:
            s = PpwecScreen(module_id=m.id, screen_number=spec.screen_number)
            service.db.add(s)
        for f in ('section', 'title', 'on_screen_text', 'voice_over', 'audio_url',
                  'caption_url', 'transcript', 'video_url', 'visual_direction',
                  'interaction_type', 'interaction_payload', 'is_mandatory',
                  'estimated_seconds', 'pulse_anchor', 'md_philosophy'):
            setattr(s, f, getattr(spec, f))

    # 3. questions (append unless replace)
    if payload.replace:
        for spec in payload.questions:
            service.db.add(PpwecQuestion(module_id=m.id, **spec.model_dump()))
    else:
        for spec in payload.questions:
            service.db.add(PpwecQuestion(module_id=m.id, **spec.model_dump()))
    service.db.commit()

    report = PpwecGovernanceService(service.db).validate_module(m.module_number)
    return {'module_number': m.module_number, 'screens': len(payload.screens),
            'questions': len(payload.questions), 'validation': report}


# ─── Governance: §Y pilot feedback, sign-offs, design freeze ────────────────

@router.post('/modules/{module_number}/pilot-feedback', status_code=status.HTTP_201_CREATED)
def submit_pilot_feedback(
    module_number: int,
    payload: PilotFeedbackRequest,
    current_user: User = Depends(get_current_user),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Pilot participants (any role) rate the module once (§Y survey)."""
    from app.services.ppwec_governance_service import PpwecGovernanceService
    try:
        return PpwecGovernanceService(service.db).submit_pilot_feedback(
            module_number, current_user.id,
            engagement=payload.engagement, relevance=payload.relevance,
            realism=payload.realism, clarity=payload.clarity,
            comments=payload.comments, function_tag=payload.function_tag,
        )
    except ValueError as e:
        _handle(e)


@router.get('/admin/modules/{module_number}/gate')
def gate_status(
    module_number: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Full §Y gate status: stage sign-offs, pilot aggregate, freeze state."""
    from app.services.ppwec_governance_service import PpwecGovernanceService
    try:
        return PpwecGovernanceService(service.db).gate_status(module_number)
    except ValueError as e:
        _handle(e)


@router.post('/admin/modules/{module_number}/signoff', status_code=status.HTTP_201_CREATED)
def record_signoff(
    module_number: int,
    payload: SignoffRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """Record a reviewer decision at one gate stage (admin records on behalf
    of the named reviewer; the audit trail keeps who/when)."""
    from app.services.ppwec_governance_service import PpwecGovernanceService
    try:
        return PpwecGovernanceService(service.db).record_signoff(
            module_number, current_user, payload.stage,
            decision=payload.decision, notes=payload.notes,
        )
    except ValueError as e:
        _handle(e)


@router.post('/admin/modules/{module_number}/freeze', status_code=status.HTTP_200_OK)
def design_freeze(
    module_number: int,
    payload: FreezeRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    service: PpwecService = Depends(get_ppwec_service),
):
    """§28 Design Freeze toggle — freezes the template for Modules 2-18."""
    from app.services.ppwec_governance_service import PpwecGovernanceService
    try:
        return PpwecGovernanceService(service.db).set_design_freeze(
            module_number, current_user, payload.frozen)
    except ValueError as e:
        _handle(e)


# ─── Admin/HR: reporting (S24) ───────────────────────────────────────────────

@router.get('/reports/organization')
def org_report(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: PpwecService = Depends(get_ppwec_service),
):
    return service.org_report()


@router.get('/reports/departments')
def department_report(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: PpwecService = Depends(get_ppwec_service),
):
    return service.department_report()


@router.get('/reports/export')
def export_csv(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: PpwecService = Depends(get_ppwec_service),
):
    csv_text = service.csv_report()
    return Response(
        content=csv_text,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="ppwec_progress.csv"'},
    )
