# """Higgsfield MCP provider v2 — aligned to the REAL mcp.higgsfield.ai contract.

# Discovered from the live server (June 2026):
#   - generate_video / generate_image take a single "params" object:
#       {"params": {"model": "<id from models_explore>", "prompt": "...",
#                   "aspect_ratio": "9:16", "duration": <sec>,
#                   "medias": [{"value": "<uuid-or-job-id>", "role": "image|audio|..."}]}}
#   - Media must be registered first via media_import_url (returns a UUID);
#     raw https:// URLs are NOT accepted inside medias.
#   - Model IDs come from the models_explore catalog (e.g. the Seedance 2.0 entry).
#   - Generations return a job id; poll job_status until a media URL appears.

# OAuth tokens are cached in backend/assets/hf_mcp_tokens.json (already done on
# this machine if setup_higgsfield.py section 7 passed).
# """
# import asyncio
# import hashlib
# import httpx
# import json
# import logging
# import re
# import random
# from http.server import BaseHTTPRequestHandler, HTTPServer
# from pathlib import Path
# from typing import Any
# from urllib.parse import parse_qs, urlparse

# from mcp import types as mcp_types
# from mcp.client.auth import OAuthClientProvider, TokenStorage
# from mcp.client.session import ClientSession
# from mcp.client.streamable_http import streamablehttp_client
# from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

# from ..config import get_settings

# logger = logging.getLogger(__name__)

# TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "hf_mcp_tokens.json"
# TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)

# # Disk-persistent map of file-content-hash -> Higgsfield media UUID. Once a file
# # has been successfully uploaded, it is NEVER re-uploaded again (across runs and
# # restarts), which is the strongest defense against Higgsfield's flaky
# # media_upload endpoint.
# MEDIA_UPLOAD_CACHE = TOKENS_PATH.parent / "hf_media_uploads.json"


# def _load_upload_cache() -> dict:
#     if MEDIA_UPLOAD_CACHE.exists():
#         try:
#             return json.loads(MEDIA_UPLOAD_CACHE.read_text(encoding="utf-8"))
#         except Exception:  # noqa: BLE001
#             return {}
#     return {}


# def _save_upload_cache(d: dict) -> None:
#     try:
#         MEDIA_UPLOAD_CACHE.write_text(json.dumps(d, indent=2), encoding="utf-8")
#     except Exception as e:  # noqa: BLE001
#         logger.warning("could not persist media upload cache: %s", e)


# def _file_hash(path: Path) -> str:
#     h = hashlib.sha256()
#     h.update(path.read_bytes())
#     return h.hexdigest()

# PENDING_AUTH_URL: dict[str, str | None] = {"url": None}

# POLL_INTERVAL_S = 8
# POLL_TIMEOUT_S = 25 * 60

# UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# MEDIA_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+\.(?:mp4|png|jpe?g|webp|webm|mov)[^\s\"'<>\\]*", re.I)


# # ── OAuth plumbing (unchanged — your tokens already work) ─
# class FileTokenStorage(TokenStorage):
#     def _read(self) -> dict:
#         if TOKENS_PATH.exists():
#             try:
#                 return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
#             except Exception:  # noqa: BLE001
#                 return {}
#         return {}

#     def _write(self, data: dict):
#         TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

#     async def get_tokens(self) -> OAuthToken | None:
#         d = self._read().get("tokens")
#         return OAuthToken(**d) if d else None

#     async def set_tokens(self, tokens: OAuthToken) -> None:
#         d = self._read()
#         d["tokens"] = tokens.model_dump(exclude_none=True)
#         self._write(d)

#     async def get_client_info(self) -> OAuthClientInformationFull | None:
#         d = self._read().get("client_info")
#         return OAuthClientInformationFull(**d) if d else None

#     async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
#         d = self._read()
#         d["client_info"] = json.loads(client_info.model_dump_json(exclude_none=True))
#         self._write(d)


# async def _redirect_handler(authorization_url: str) -> None:
#     PENDING_AUTH_URL["url"] = authorization_url
#     logger.warning("HIGGSFIELD SIGN-IN REQUIRED — open this URL:\n%s", authorization_url)
#     try:
#         import webbrowser
#         webbrowser.open(authorization_url)
#     except Exception:  # noqa: BLE001
#         pass


# async def _callback_handler() -> tuple[str, str | None]:
#     s = get_settings()
#     result: dict[str, Any] = {}

#     class Handler(BaseHTTPRequestHandler):
#         def do_GET(self):  # noqa: N802
#             q = parse_qs(urlparse(self.path).query)
#             result["code"] = (q.get("code") or [None])[0]
#             result["state"] = (q.get("state") or [None])[0]
#             self.send_response(200)
#             self.send_header("Content-Type", "text/html")
#             self.end_headers()
#             self.wfile.write(b"<h2>Higgsfield connected. You can close this tab.</h2>")

#         def log_message(self, *a):
#             pass

#     server = HTTPServer(("127.0.0.1", s.hf_mcp_callback_port), Handler)
#     loop = asyncio.get_event_loop()

#     def serve():
#         while "code" not in result:
#             server.handle_request()

#     await loop.run_in_executor(None, serve)
#     server.server_close()
#     PENDING_AUTH_URL["url"] = None
#     if not result.get("code"):
#         raise RuntimeError("OAuth callback received no authorization code")
#     return result["code"], result.get("state")


# def _oauth_provider() -> OAuthClientProvider:
#     s = get_settings()
#     return OAuthClientProvider(
#         server_url=s.hf_mcp_url,
#         client_metadata=OAuthClientMetadata(
#             client_name="Reel Studio",
#             redirect_uris=[f"http://127.0.0.1:{s.hf_mcp_callback_port}/callback"],
#             grant_types=["authorization_code", "refresh_token"],
#             response_types=["code"],
#         ),
#         storage=FileTokenStorage(),
#         redirect_handler=_redirect_handler,
#         callback_handler=_callback_handler,
#     )


# # ── Result helpers ────────────────────────────────────────
# def _result_text(res: mcp_types.CallToolResult) -> str:
#     parts = []
#     if res.structuredContent:
#         parts.append(json.dumps(res.structuredContent))
#     for c in res.content or []:
#         if isinstance(c, mcp_types.TextContent):
#             parts.append(c.text)
#     return "\n".join(parts)


# def _wrap_if_needed(schema: dict, inner: dict) -> dict:
#     """The live server nests args under 'params'; wrap when the schema says so."""
#     props = (schema or {}).get("properties", {})
#     if "params" in props:
#         return {"params": inner}
#     return inner


# def _loads_any(text: str):
#     """Parse the first JSON object/array found in a tool response string."""
#     try:
#         start = min([i for i in (text.find("{"), text.find("[")) if i != -1])
#         end = max(text.rfind("}"), text.rfind("]")) + 1
#         return json.loads(text[start:end])
#     except Exception:  # noqa: BLE001
#         return {}


# def _deep_find(obj, keys: tuple):
#     """Find the first value for any of `keys` anywhere in a nested dict/list."""
#     if isinstance(obj, dict):
#         for k in keys:
#             if k in obj and obj[k]:
#                 return obj[k]
#         for v in obj.values():
#             r = _deep_find(v, keys)
#             if r:
#                 return r
#     elif isinstance(obj, list):
#         for v in obj:
#             r = _deep_find(v, keys)
#             if r:
#                 return r
#     return None


# def _detect_preset_recommendation(text: str) -> str | None:
#     """If Higgsfield returns a preset_recommendation notice instead of submitting
#     a job, return the recommended preset id so we can decline it and force a
#     literal generation. Returns None if no preset recommendation."""
#     data = _loads_any(text)
#     if isinstance(data, dict):
#         notice = data.get("notice")
#         if isinstance(notice, dict) and notice.get("type") == "preset_recommendation":
#             preset = (notice.get("data") or {}).get("preset") or {}
#             return preset.get("id")
#     # textual fallback
#     if "preset_recommendation" in text:
#         m = re.search(r'"preset"\s*:\s*\{[^}]*"id"\s*:\s*"([^"]+)"', text)
#         if m:
#             return m.group(1)
#     return None


# def _has_real_job(text: str) -> bool:
#     """True if the response actually created a job (results[].id present)."""
#     data = _loads_any(text)
#     if isinstance(data, dict):
#         r = data.get("results")
#         if isinstance(r, list) and r and isinstance(r[0], dict) and r[0].get("id"):
#             return True
#         if isinstance(data.get("generation"), dict) and data["generation"].get("id"):
#             return True
#     return False


# def _find_job_id(text: str) -> str | None:
#     """Extract the JOB id from a submit response. The real shape is
#     {"results":[{"id":"<job-id>", "type":"video", "params":{"medias":[{"data":{"id":"<MEDIA-id>"}}]}}]}.
#     We must take results[0].id (the job), NOT a nested media data.id, and NOT a
#     stale id elsewhere in the text. Parse structurally and fall back carefully."""
#     data = _loads_any(text)
#     # Preferred: results[].id  (or generation.id)
#     if isinstance(data, dict):
#         results = data.get("results")
#         if isinstance(results, list) and results and isinstance(results[0], dict):
#             jid = results[0].get("id")
#             if jid:
#                 return str(jid)
#         gen = data.get("generation")
#         if isinstance(gen, dict) and gen.get("id"):
#             return str(gen["id"])
#         # explicit job id keys at top level
#         for k in ("job_id", "jobId", "generation_id", "request_id", "task_id"):
#             if data.get(k):
#                 return str(data[k])
#     # Last resort: a top-level "id" that is NOT inside a "data" block.
#     # Strip nested data:{...} objects so we don't grab a media id.
#     stripped = re.sub(r'"data"\s*:\s*\{[^{}]*\}', '', text)
#     m = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{36})"', stripped)
#     return m.group(1) if m else None


# # ── Main client ───────────────────────────────────────────
# class HiggsfieldMCP:
#     """One instance per pipeline job. Caches tool schemas, model ids, and
#     imported-media UUIDs across calls."""

#     def __init__(self, job: dict | None = None):
#         self._schemas: dict[str, dict] = {}
#         self._model_cache: dict[str, str] = {}       # hint -> model id
#         self._media_cache: dict[str, str] = {}       # source url -> media uuid
#         self._catalog_text: str = ""
#         self._job = job  # optional job dict for cost accounting

#     async def _get_cost(self, session: ClientSession, tool: str, inner: dict) -> float | None:
#         """Ask the server for the credit cost without submitting (get_cost=True)."""
#         try:
#             probe = {**inner, "get_cost": True}
#             text = await self._call(session, tool, probe)
#             m = re.search(r'"credits(?:_exact)?"\s*:\s*([0-9.]+)', text)
#             return float(m.group(1)) if m else None
#         except Exception as e:  # noqa: BLE001
#             logger.warning("get_cost preflight failed (continuing): %s", e)
#             return None

#     # -- session plumbing --------------------------------------------------
#     async def _with_session(self, fn):
#         s = get_settings()
#         async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
#                                          timeout=180) as (read, write, _):
#             async with ClientSession(read, write) as session:
#                 await session.initialize()
#                 if not self._schemas:
#                     tools = await session.list_tools()
#                     self._schemas = {t.name: (t.inputSchema or {}) for t in tools.tools}
#                 return await fn(session)

#     async def _call(self, session: ClientSession, tool: str, inner: dict,
#                     raw: bool = False) -> str:
#         args = inner if raw else _wrap_if_needed(self._schemas.get(tool, {}), inner)
#         logger.info("MCP %s | %s", tool, json.dumps(args)[:400])
#         res = await session.call_tool(tool, args)
#         text = _result_text(res)
#         if res.isError:
#             raise RuntimeError(f"MCP {tool} error: {text[:500]}")
#         return text

#     # -- model catalog -----------------------------------------------------
#     # Verified model IDs on this account (from models_explore)
#     EXACT_IDS = {"video": "seedance_2_0", "image": "gpt_image_2"}

#     async def _resolve_model(self, session: ClientSession, hint: str, kind: str) -> str:
#         if kind in self.EXACT_IDS:
#             return self.EXACT_IDS[kind]
#         cache_key = f"{kind}:{hint}"
#         if cache_key in self._model_cache:
#             return self._model_cache[cache_key]

#         # models_explore requires {"action": "list"|"search"|"get"|"recommend"}
#         text = ""
#         attempts = [
#             {"action": "search", "query": hint},
#             {"action": "search", "q": hint},
#             {"action": "list"},
#         ]
#         last_err = None
#         for args in attempts:
#             try:
#                 text = await self._call(session, "models_explore", args, raw=True)
#                 if text:
#                     break
#             except Exception as e:  # noqa: BLE001
#                 last_err = e
#                 continue
#         if not text:
#             raise RuntimeError(f"models_explore failed: {last_err}")
#         self._catalog_text = text
#         norm = hint.lower().replace(" ", "").replace("-", "").replace(".", "").replace("_", "")

#         # candidate model ids: any quoted string value whose normalized form contains the hint
#         candidates = []
#         for m in re.finditer(r'"([A-Za-z0-9._\-/ ]{3,60})"', text):
#             v = m.group(1)
#             vn = v.lower().replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
#             if norm in vn:
#                 candidates.append(v)
#         # prefer id-looking strings (snake/kebab, no spaces), highest version
#         ids = [c for c in candidates if " " not in c] or candidates
#         if not ids:
#             raise RuntimeError(
#                 f"No model matching '{hint}' in your Higgsfield catalog. "
#                 f"Catalog excerpt: {text[:600]}"
#             )
#         model = sorted(set(ids))[-1]
#         logger.info("Resolved model hint '%s' (%s) -> '%s' (candidates: %s)",
#                     hint, kind, model, sorted(set(ids))[:6])
#         self._model_cache[cache_key] = model
#         return model

#     # -- media import ------------------------------------------------------
#     _CT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
#            ".gif": "image/gif", ".webp": "image/webp", ".wav": "audio/wav",
#            ".mp4": "video/mp4"}
#     _MEDIA_KIND = {"image/png": "image", "image/jpeg": "image", "image/gif": "image",
#                    "image/webp": "image", "audio/wav": "audio", "video/mp4": "video"}

#     async def _upload_media_file(self, session: ClientSession, path) -> str:
#         """Official Higgsfield upload path (no URL fetch, so no 'fetch failed'):
#         media_upload -> get presigned PUT url + media id -> PUT bytes -> media_confirm.
#         Returns the confirmed media UUID.

#         ROBUSTNESS: results are cached BOTH in-memory (per path) and ON DISK
#         (per file-content hash). A file whose bytes were uploaded once is never
#         re-uploaded again — across jobs and across server restarts. This is the
#         primary defense against Higgsfield's intermittent media_upload errors."""
#         from pathlib import Path as _P
#         import asyncio as _asyncio
#         path = _P(path)

#         # Higgsfield's media_upload is picky about formats (audio must be WAV).
#         # Its presigned-URL generation has been seen to fail on raw JPEG, so
#         # normalize any non-PNG image to PNG first — same idea as forcing WAV for
#         # audio. PNG is the format its generated images use and uploads reliably.
#         if path.suffix.lower() in (".jpg", ".jpeg", ".webp", ".gif"):
#             try:
#                 from PIL import Image
#                 png_path = path.with_suffix(".png")
#                 if not png_path.exists():
#                     with Image.open(path) as im:
#                         im.convert("RGB").save(png_path, "PNG")
#                 logger.info("media_upload: converted %s -> %s (PNG) for reliable upload",
#                             path.name, png_path.name)
#                 path = png_path
#             except ImportError:
#                 logger.error("Pillow (PIL) is NOT installed — cannot convert %s to PNG. "
#                              "Run: pip install Pillow  (then restart). Uploading original JPEG "
#                              "for now, which Higgsfield's media_upload may reject.", path.name)
#             except Exception as e:  # noqa: BLE001
#                 logger.warning("PNG conversion failed for %s (uploading original): %s", path.name, e)

#         key = str(path.resolve())
#         if key in self._media_cache:
#             return self._media_cache[key]

#         ext = path.suffix.lower()
#         ctype = self._CT.get(ext)
#         if not ctype:
#             raise RuntimeError(f"Unsupported media type '{ext}' for {path.name}")
#         kind = self._MEDIA_KIND.get(ctype, "file")

#         # 0) Disk cache by content hash — skip the network entirely if we've
#         #    already uploaded these exact bytes before.
#         fhash = _file_hash(path)
#         disk = _load_upload_cache()
#         if fhash in disk and disk[fhash].get("media_id"):
#             mid = disk[fhash]["media_id"]
#             self._media_cache[key] = mid
#             logger.info("media_upload: reusing previously-uploaded %s (cached id %s)", path.name, mid)
#             return mid

#         # 1) request a presigned upload slot — durable retry with exponential
#         #    backoff + jitter. Up to 10 attempts over ~3 minutes.
#         # Use a UNIQUE filename each attempt: some servers choke on repeated
#         # identical filenames ("host.jpg") sent in quick succession.
#         import uuid as _uuid
#         unique_name = f"{path.stem}_{_uuid.uuid4().hex[:8]}{path.suffix}"
#         up_url = media_id = None
#         last_err = None
#         MAX_TRIES = 10
#         for attempt in range(1, MAX_TRIES + 1):
#             try:
#                 text = await self._call(session, "media_upload",
#                                         {"filename": unique_name, "content_type": ctype})
#                 data = _loads_any(text)
#                 up_url = _deep_find(data, ("upload_url", "url", "put_url", "presigned_url", "uploadUrl"))
#                 media_id = _deep_find(data, ("media_id", "id", "uuid", "mediaId"))
#                 if not up_url or not media_id:
#                     files = _deep_find(data, ("files",))
#                     if isinstance(files, list) and files:
#                         up_url = up_url or files[0].get("upload_url") or files[0].get("url")
#                         media_id = media_id or files[0].get("media_id") or files[0].get("id")
#                 if up_url and media_id:
#                     break
#                 last_err = RuntimeError(f"media_upload gave no upload_url/media_id: {text[:300]}")
#             except Exception as e:  # noqa: BLE001
#                 last_err = e
#                 msg = str(e).lower()
#                 transient = ("something went wrong" in msg or "try again" in msg
#                              or "timeout" in msg or "timed out" in msg
#                              or "502" in msg or "503" in msg or "504" in msg
#                              or "rate" in msg or "temporarily" in msg)
#                 if not transient:
#                     raise
#             if attempt < MAX_TRIES:
#                 # exponential backoff with jitter: ~2,3,5,8,12,16,20,20,20s
#                 backoff = min(2 ** min(attempt, 4) + attempt, 20) + random.uniform(0, 2)
#                 logger.warning("media_upload transient error %d/%d for %s: %s; retry in %.1fs",
#                                attempt, MAX_TRIES, path.name, str(last_err)[:120], backoff)
#                 await _asyncio.sleep(backoff)
#         if not up_url or not media_id:
#             raise RuntimeError(
#                 f"media_upload for {path.name} failed after {MAX_TRIES} attempts. "
#                 f"This is a Higgsfield server-side issue (media_upload). Last error: {last_err}")

#         # 2) PUT the raw bytes to the presigned url — retried with jitter
#         put_ok = False
#         for attempt in range(1, 6):
#             try:
#                 async with httpx.AsyncClient(timeout=300) as client:
#                     r = await client.put(up_url, content=path.read_bytes(),
#                                          headers={"Content-Type": ctype})
#                     r.raise_for_status()
#                 put_ok = True
#                 break
#             except Exception as e:  # noqa: BLE001
#                 last_err = e
#                 backoff = min(2 * attempt, 10) + random.uniform(0, 1.5)
#                 logger.warning("media PUT error %d/5 for %s: %s; retry in %.1fs",
#                                attempt, path.name, str(e)[:120], backoff)
#                 await _asyncio.sleep(backoff)
#         if not put_ok:
#             raise RuntimeError(f"media PUT for {path.name} failed after retries: {last_err}")

#         # 3) confirm (best-effort)
#         try:
#             await self._call(session, "media_confirm",
#                             {"type": kind, "media_id": media_id})
#         except Exception as e:  # noqa: BLE001
#             logger.warning("media_confirm warning (continuing): %s", e)

#         # 4) persist to both caches so this file never uploads again
#         self._media_cache[key] = media_id
#         disk[fhash] = {"media_id": media_id, "name": path.name}
#         _save_upload_cache(disk)
#         return media_id

#     async def register_local_image(self, local_path) -> dict:
#         """Upload a local image to Higgsfield and return {"url", "ref"} where ref
#         is a reusable media UUID. Used to lock a pre-approved identity image into
#         the cache so it is NEVER regenerated."""
#         from pathlib import Path as _P
#         from . import higgsfield_video as _hv  # reuse the platform uploader

#         async def run(session):
#             ref = await self._upload_media_file(session, _P(local_path))
#             return {"url": str(_P(local_path)), "ref": ref}

#         return await self._with_session(run)

#     # -- generation --------------------------------------------------------
#     async def generate(self, kind: str, *, prompt: str, model_hint: str,
#                        image_files: list | None = None,
#                        image_refs: list[str] | None = None,
#                        audio_files: list | None = None,
#                        start_frame_file=None,
#                        duration: int | None = None,
#                        aspect_ratio: str | None = None,
#                        resolution: str | None = None,
#                        character_id: str | None = None) -> dict:
#         """Returns {"url": <downloadable media url>, "ref": <reusable media id>}.
#         image_files -> LOCAL image paths, uploaded via media_upload presigned PUT.
#         image_refs  -> already-Higgsfield media ids reused directly (no re-upload).
#         audio_files -> LOCAL wav paths, uploaded the same way."""
#         async def run(session: ClientSession) -> dict:
#             tool = f"generate_{kind}"
#             if tool not in self._schemas:
#                 raise RuntimeError(f"Tool {tool} not on server; have: {list(self._schemas)[:20]}")

#             model = await self._resolve_model(session, model_hint, kind)

#             # Seedance 2.0 wants the primary conditioning image as 'start_image';
#             # GPT Image 2 edit uses 'image'. Audio reference role is 'audio'.
#             img_role = "start_image" if (kind == "video" and model == "seedance_2_0") else "image"
#             medias = []

#             # Build the ordered list of conditioning images. refs (already on
#             # Higgsfield) come first, then any local files (uploaded once). For
#             # Seedance video the FIRST image is the primary subject (start_image)
#             # and any ADDITIONAL images ride as secondary 'image' references — this
#             # is how we hand the model BOTH the speaker and the listener faces so a
#             # brief in-clip reaction cut can render the other person, while still
#             # only ever showing one person at a time.
#             ordered_img_values: list[str] = []
#             for ref in image_refs or []:               # already on Higgsfield — reference directly
#                 ordered_img_values.append(ref)
#             for f in image_files or []:                # local file -> presigned upload (once)
#                 ordered_img_values.append(await self._upload_media_file(session, f))

#             if start_frame_file is not None:
#                 # explicit continuity frame takes the start slot; all identity
#                 # images become secondary 'image' anchors
#                 start_id = await self._upload_media_file(session, start_frame_file)
#                 medias.append({"value": start_id, "role": "start_image"})
#                 for v in ordered_img_values:
#                     medias.append({"value": v, "role": "image"})
#             elif kind == "video" and model == "seedance_2_0":
#                 for i, v in enumerate(ordered_img_values):
#                     medias.append({"value": v, "role": "start_image" if i == 0 else "image"})
#             else:
#                 for v in ordered_img_values:
#                     medias.append({"value": v, "role": img_role})
#             for f in audio_files or []:
#                 medias.append({"value": await self._upload_media_file(session, f), "role": "audio"})

#             inner: dict[str, Any] = {"model": model, "prompt": prompt}
#             if aspect_ratio:
#                 inner["aspect_ratio"] = aspect_ratio
#             if duration is not None and kind == "video":
#                 inner["duration"] = int(max(4, min(15, duration)))  # seedance range
#             if resolution:
#                 # model-specific param names: seedance/gpt use params.resolution
#                 inner["resolution"] = resolution
#             if medias:
#                 inner["medias"] = medias
#             if character_id:
#                 inner["character_id"] = character_id

#             # Preflight cost (zero spend) and enforce a per-clip ceiling
#             cost = await self._get_cost(session, tool, inner)
#             if cost is not None:
#                 logger.info("%s preflight cost: %s credits", tool, cost)
#                 job = self._job
#                 if job is not None:
#                     job["cost_estimate"] = job.get("cost_estimate", 0) + cost
#                 ceiling = get_settings().hf_max_credits_per_clip
#                 if kind == "video" and ceiling and cost > ceiling:
#                     raise RuntimeError(
#                         f"Seedance clip would cost {cost} credits, over the "
#                         f"HF_MAX_CREDITS_PER_CLIP ceiling of {ceiling}. Aborting before spend."
#                     )

#             text = await self._call(session, tool, inner)

#             # Higgsfield may intercept the prompt and recommend a PRESET instead of
#             # generating (returns a preset_recommendation notice, NO job created).
#             # Decline the preset and resubmit to force literal generation. Loop a
#             # few times in case it suggests multiple presets.
#             declined: list[str] = []
#             for _ in range(4):
#                 if _has_real_job(text):
#                     break
#                 preset_id = _detect_preset_recommendation(text)
#                 if not preset_id:
#                     break
#                 logger.info("%s: declining preset recommendation %s and forcing literal generation",
#                             tool, preset_id)
#                 declined.append(preset_id)
#                 retry_inner = dict(inner)
#                 # the schema accepts a single declined_preset_id; pass the latest
#                 retry_inner["declined_preset_id"] = preset_id
#                 text = await self._call(session, tool, retry_inner)

#             job_id = _find_job_id(text)
#             logger.info("%s submit -> parsed job_id=%s | raw response head: %s",
#                         tool, job_id, text[:300].replace(chr(10), " "))

#             # VIDEO: never trust an inline URL in the submit response — Higgsfield
#             # returns a preset/sample clip there (job_set_chain_preset/...). Always
#             # poll the job to get the REAL rendered output.
#             if kind == "video":
#                 if not job_id:
#                     raise RuntimeError(f"{tool}: no job id to poll: {text[:400]}")
#                 url = await self._poll(session, job_id, prefer="video")
#                 return {"url": url, "ref": job_id}

#             # IMAGE: inline URL is fine; fall back to polling if absent.
#             inline = self._real_media_url(text, prefer="image")
#             if inline:
#                 return {"url": inline, "ref": job_id}
#             if not job_id:
#                 raise RuntimeError(f"{tool}: no media URL or job id: {text[:400]}")
#             url = await self._poll(session, job_id, prefer="image")
#             return {"url": url, "ref": job_id}

#         return await self._with_session(run)

#     @staticmethod
#     def _real_media_url(text: str, prefer: str = "any") -> str | None:
#         """Return a finished-output media URL, ignoring preset/sample decoys and
#         input-media echoes. prefer='video' picks .mp4/.webm/.mov; 'image' picks
#         image types; 'any' returns the first acceptable url."""
#         def bad(u: str) -> bool:
#             ul = u.lower()
#             return ("job_set_chain_preset" in ul or "/preset" in ul or "sample" in ul
#                     # input media is echoed back in params; outputs live on cdn.higgsfield.ai
#                     or "d2ol7oe51mr4n9.cloudfront.net" in ul)
#         vids, imgs, other = [], [], []
#         for m in MEDIA_URL_RE.finditer(text):
#             u = m.group(0)
#             if bad(u):
#                 continue
#             ul = u.lower()
#             if ul.split("?")[0].endswith((".mp4", ".webm", ".mov")):
#                 vids.append(u)
#             elif ul.split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
#                 imgs.append(u)
#             else:
#                 other.append(u)
#         if prefer == "video":
#             return (vids or other or imgs or [None])[0]
#         if prefer == "image":
#             return (imgs or other or vids or [None])[0]
#         return (vids or imgs or other or [None])[0]

#     async def _poll(self, session: ClientSession, job_id: str, prefer: str = "any") -> str:
#         """Poll job_status using the REAL schema: param is 'jobId' (camelCase),
#         and sync:true makes the server wait up to ~25s and return on terminal
#         state. Transient server errors during polling are retried (the job keeps
#         rendering on Higgsfield's side regardless of a flaky status call)."""
#         waited = 0
#         consecutive_errors = 0
#         MAX_CONSECUTIVE_ERRORS = 15
#         while waited < POLL_TIMEOUT_S:
#             try:
#                 text = await self._call(session, "job_status", {"jobId": job_id, "sync": True})
#                 consecutive_errors = 0
#             except Exception as e:  # noqa: BLE001
#                 msg = str(e)
#                 # A real generation failure can surface as an error payload — only
#                 # treat explicit job-failure statuses as fatal; everything else is
#                 # a transient status-endpoint hiccup ("Something went wrong", 5xx).
#                 if re.search(r'"status"\s*:\s*"(failed|nsfw|rejected|canceled|cancelled)"', msg, re.I):
#                     raise
#                 consecutive_errors += 1
#                 logger.warning("job_status transient error %d/%d for %s: %s",
#                                consecutive_errors, MAX_CONSECUTIVE_ERRORS, job_id, msg[:160])
#                 if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
#                     raise RuntimeError(
#                         f"job_status kept failing for {job_id} after "
#                         f"{MAX_CONSECUTIVE_ERRORS} tries: {msg[:200]}")
#                 await asyncio.sleep(12)
#                 waited += 12
#                 continue

#             failed = re.search(r'"status"\s*:\s*"(failed|nsfw|error|canceled|cancelled|rejected)"', text, re.I)
#             if failed:
#                 raise RuntimeError(f"Higgsfield generation failed: {text[:500]}")

#             done = re.search(r'"status"\s*:\s*"(completed|succeeded|success|done|ready|finished)"', text, re.I)
#             if done:
#                 url = self._real_media_url(text, prefer)
#                 if url:
#                     return url
#                 for reveal in ("job_display", "reveal_generation", "show_generations"):
#                     if reveal in self._schemas:
#                         try:
#                             t2 = await self._call(session, reveal, {"jobId": job_id})
#                             url = self._real_media_url(t2, prefer)
#                             if url:
#                                 return url
#                         except Exception:  # noqa: BLE001
#                             continue
#                 raise RuntimeError(f"Job {job_id} completed but no real media URL: {text[:500]}")

#             # sync:true already waited ~25s server-side; brief client gap then retry
#             await asyncio.sleep(3)
#             waited += 28
#         raise TimeoutError(f"Generation {job_id} timed out after {POLL_TIMEOUT_S}s")




"""Higgsfield MCP provider v2 — aligned to the REAL mcp.higgsfield.ai contract.

Discovered from the live server (June 2026):
  - generate_video / generate_image take a single "params" object:
      {"params": {"model": "<id from models_explore>", "prompt": "...",
                  "aspect_ratio": "9:16", "duration": <sec>,
                  "medias": [{"value": "<uuid-or-job-id>", "role": "image|audio|..."}]}}
  - Media must be registered first via media_import_url (returns a UUID);
    raw https:// URLs are NOT accepted inside medias.
  - Model IDs come from the models_explore catalog (e.g. the Seedance 2.0 entry).
  - Generations return a job id; poll job_status until a media URL appears.

OAuth tokens are cached in backend/assets/hf_mcp_tokens.json (already done on
this machine if setup_higgsfield.py section 7 passed).
"""
import asyncio
import hashlib
import httpx
import json
import logging
import re
import random
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp import types as mcp_types
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from ..config import get_settings

logger = logging.getLogger(__name__)

TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "hf_mcp_tokens.json"
TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)

# Disk-persistent map of file-content-hash -> Higgsfield media UUID. Once a file
# has been successfully uploaded, it is NEVER re-uploaded again (across runs and
# restarts), which is the strongest defense against Higgsfield's flaky
# media_upload endpoint.
MEDIA_UPLOAD_CACHE = TOKENS_PATH.parent / "hf_media_uploads.json"


def _load_upload_cache() -> dict:
    if MEDIA_UPLOAD_CACHE.exists():
        try:
            return json.loads(MEDIA_UPLOAD_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_upload_cache(d: dict) -> None:
    try:
        MEDIA_UPLOAD_CACHE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not persist media upload cache: %s", e)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

PENDING_AUTH_URL: dict[str, str | None] = {"url": None}

POLL_INTERVAL_S = 8
POLL_TIMEOUT_S = 25 * 60

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MEDIA_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+\.(?:mp4|png|jpe?g|webp|webm|mov)[^\s\"'<>\\]*", re.I)


# ── OAuth plumbing (unchanged — your tokens already work) ─
class FileTokenStorage(TokenStorage):
    def _read(self) -> dict:
        if TOKENS_PATH.exists():
            try:
                return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _write(self, data: dict):
        TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        d = self._read().get("tokens")
        return OAuthToken(**d) if d else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        d = self._read()
        d["tokens"] = tokens.model_dump(exclude_none=True)
        self._write(d)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        d = self._read().get("client_info")
        return OAuthClientInformationFull(**d) if d else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        d = self._read()
        d["client_info"] = json.loads(client_info.model_dump_json(exclude_none=True))
        self._write(d)


async def _redirect_handler(authorization_url: str) -> None:
    PENDING_AUTH_URL["url"] = authorization_url
    logger.warning("HIGGSFIELD SIGN-IN REQUIRED — open this URL:\n%s", authorization_url)
    try:
        import webbrowser
        webbrowser.open(authorization_url)
    except Exception:  # noqa: BLE001
        pass


async def _callback_handler() -> tuple[str, str | None]:
    s = get_settings()
    result: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            result["code"] = (q.get("code") or [None])[0]
            result["state"] = (q.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Higgsfield connected. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", s.hf_mcp_callback_port), Handler)
    loop = asyncio.get_event_loop()

    def serve():
        while "code" not in result:
            server.handle_request()

    await loop.run_in_executor(None, serve)
    server.server_close()
    PENDING_AUTH_URL["url"] = None
    if not result.get("code"):
        raise RuntimeError("OAuth callback received no authorization code")
    return result["code"], result.get("state")


def _oauth_provider() -> OAuthClientProvider:
    s = get_settings()
    return OAuthClientProvider(
        server_url=s.hf_mcp_url,
        client_metadata=OAuthClientMetadata(
            client_name="Reel Studio",
            redirect_uris=[f"http://localhost:{s.hf_mcp_callback_port}/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=FileTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )


# ── Result helpers ────────────────────────────────────────
def _result_text(res: mcp_types.CallToolResult) -> str:
    parts = []
    if res.structuredContent:
        parts.append(json.dumps(res.structuredContent))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent):
            parts.append(c.text)
    return "\n".join(parts)


def _wrap_if_needed(schema: dict, inner: dict) -> dict:
    """The live server nests args under 'params'; wrap when the schema says so."""
    props = (schema or {}).get("properties", {})
    if "params" in props:
        return {"params": inner}
    return inner


def _loads_any(text: str):
    """Parse the first JSON object/array found in a tool response string."""
    try:
        start = min([i for i in (text.find("{"), text.find("[")) if i != -1])
        end = max(text.rfind("}"), text.rfind("]")) + 1
        return json.loads(text[start:end])
    except Exception:  # noqa: BLE001
        return {}


def _deep_find(obj, keys: tuple):
    """Find the first value for any of `keys` anywhere in a nested dict/list."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k]:
                return obj[k]
        for v in obj.values():
            r = _deep_find(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, keys)
            if r:
                return r
    return None


def _detect_preset_recommendation(text: str) -> str | None:
    """If Higgsfield returns a preset_recommendation notice instead of submitting
    a job, return the recommended preset id so we can decline it and force a
    literal generation. Returns None if no preset recommendation."""
    data = _loads_any(text)
    if isinstance(data, dict):
        notice = data.get("notice")
        if isinstance(notice, dict) and notice.get("type") == "preset_recommendation":
            preset = (notice.get("data") or {}).get("preset") or {}
            return preset.get("id")
    # textual fallback
    if "preset_recommendation" in text:
        m = re.search(r'"preset"\s*:\s*\{[^}]*"id"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return None


def _has_real_job(text: str) -> bool:
    """True if the response actually created a job (results[].id present)."""
    data = _loads_any(text)
    if isinstance(data, dict):
        r = data.get("results")
        if isinstance(r, list) and r and isinstance(r[0], dict) and r[0].get("id"):
            return True
        if isinstance(data.get("generation"), dict) and data["generation"].get("id"):
            return True
    return False


def _find_job_id(text: str) -> str | None:
    """Extract the JOB id from a submit response. The real shape is
    {"results":[{"id":"<job-id>", "type":"video", "params":{"medias":[{"data":{"id":"<MEDIA-id>"}}]}}]}.
    We must take results[0].id (the job), NOT a nested media data.id, and NOT a
    stale id elsewhere in the text. Parse structurally and fall back carefully."""
    data = _loads_any(text)
    # Preferred: results[].id  (or generation.id)
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            jid = results[0].get("id")
            if jid:
                return str(jid)
        gen = data.get("generation")
        if isinstance(gen, dict) and gen.get("id"):
            return str(gen["id"])
        # explicit job id keys at top level
        for k in ("job_id", "jobId", "generation_id", "request_id", "task_id"):
            if data.get(k):
                return str(data[k])
    # Last resort: a top-level "id" that is NOT inside a "data" block.
    # Strip nested data:{...} objects so we don't grab a media id.
    stripped = re.sub(r'"data"\s*:\s*\{[^{}]*\}', '', text)
    m = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{36})"', stripped)
    return m.group(1) if m else None


# ── Main client ───────────────────────────────────────────
class HiggsfieldMCP:
    """One instance per pipeline job. Caches tool schemas, model ids, and
    imported-media UUIDs across calls."""

    def __init__(self, job: dict | None = None):
        self._schemas: dict[str, dict] = {}
        self._model_cache: dict[str, str] = {}       # hint -> model id
        self._media_cache: dict[str, str] = {}       # source url -> media uuid
        self._catalog_text: str = ""
        self._job = job  # optional job dict for cost accounting

    async def _get_cost(self, session: ClientSession, tool: str, inner: dict) -> float | None:
        """Ask the server for the credit cost without submitting (get_cost=True)."""
        try:
            probe = {**inner, "get_cost": True}
            text = await self._call(session, tool, probe)
            m = re.search(r'"credits(?:_exact)?"\s*:\s*([0-9.]+)', text)
            return float(m.group(1)) if m else None
        except Exception as e:  # noqa: BLE001
            logger.warning("get_cost preflight failed (continuing): %s", e)
            return None

    # -- session plumbing --------------------------------------------------
    async def _with_session(self, fn):
        s = get_settings()
        async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
                                         timeout=180) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if not self._schemas:
                    tools = await session.list_tools()
                    self._schemas = {t.name: (t.inputSchema or {}) for t in tools.tools}
                return await fn(session)

    async def _call(self, session: ClientSession, tool: str, inner: dict,
                    raw: bool = False) -> str:
        args = inner if raw else _wrap_if_needed(self._schemas.get(tool, {}), inner)
        logger.info("MCP %s | %s", tool, json.dumps(args)[:400])
        res = await session.call_tool(tool, args)
        text = _result_text(res)
        if res.isError:
            raise RuntimeError(f"MCP {tool} error: {text[:500]}")
        return text

    # -- model catalog -----------------------------------------------------
    # Verified fast-path IDs for the DEFAULT family. Overridable via config
    # `hf_mcp_video_model_hint` — when the user selects a non-default family
    # (e.g. 'kling'), the video branch skips this shortcut and searches the
    # live catalog for the newest variant matching the configured family.
    EXACT_IDS = {"video": "seedance_2_0", "image": "gpt_image_2"}

    # When the configured video family is one of these, we know the fast-path
    # id is the right one and can skip a catalog roundtrip. Any other family
    # goes through _fetch_catalog + _rank_video_candidates.
    _FAST_PATH_VIDEO_FAMILIES = {"seedance"}

    async def _resolve_model(self, session: ClientSession, hint: str, kind: str) -> str:
        """Resolve a model id from a hint.

        Video path is CONFIG-DRIVEN via `hf_mcp_video_model_hint` in
        backend/.env:

          * hf_mcp_video_model_hint=seedance  (default)  → pins to
            'seedance_2_0' — Higgsfield's reference-driven Seedance
            ("Reference-driven video with image/video/audio reference inputs,
            consistent identity, multi-SKU"). Roles: start_image + one or
            more image_references + audio_references.

          * hf_mcp_video_model_hint=kling  → searches the catalog for the
            newest Kling variant on your plan (preferring Omni / O3 / 3.0
            over older 2.x). Roles are then read from THAT model's catalog
            entry via _model_roles(); generate() picks the right one from a
            broad candidate list automatically.

        Any other value (e.g. 'sora', 'veo') is treated the same way —
        catalog search, best-variant pick, per-model roles.
        """
        if kind == "video":
            try:
                family_raw = (get_settings().hf_mcp_video_model_hint or "").strip().lower()
            except Exception:  # noqa: BLE001 — settings problems shouldn't kill generation
                family_raw = ""
            family = family_raw or "seedance"

            if family in self._FAST_PATH_VIDEO_FAMILIES:
                # Fast path — no catalog roundtrip needed for the default.
                return self.EXACT_IDS["video"]

            # Non-default family: search the catalog. Cache per family so we
            # only pay the roundtrip once per session.
            cache_key = f"video:family:{family}"
            if cache_key in self._model_cache:
                return self._model_cache[cache_key]

            text = await self._fetch_catalog(session, family)
            norm = family.replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
            candidates: list[str] = []
            for m in re.finditer(r'"id"\s*:\s*"([A-Za-z0-9._\-/]{3,80})"', text):
                v = m.group(1)
                vn = v.lower().replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
                if norm in vn:
                    candidates.append(v)
            candidates = sorted(set(candidates))
            if not candidates:
                raise RuntimeError(
                    f"hf_mcp_video_model_hint='{family}' but no matching model found in your "
                    f"Higgsfield catalog. Either the family name is wrong or your plan doesn't "
                    f"expose it. Run `python diagnose_seedance_deep.py {family}` to inspect. "
                    f"Catalog excerpt: {text[:400]}"
                )
            model = self._rank_video_candidates(candidates)
            logger.info(
                "Resolved video family '%s' -> '%s' (candidates: %s)",
                family, model, candidates[:8],
            )
            self._model_cache[cache_key] = model
            return model

        # Non-video kinds: unchanged behaviour.
        if kind in self.EXACT_IDS:
            return self.EXACT_IDS[kind]
        cache_key = f"{kind}:{hint}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        text = await self._fetch_catalog(session, hint)
        norm = hint.lower().replace(" ", "").replace("-", "").replace(".", "").replace("_", "")

        # candidate model ids: any quoted string value whose normalized form contains the hint
        candidates = []
        for m in re.finditer(r'"([A-Za-z0-9._\-/ ]{3,60})"', text):
            v = m.group(1)
            vn = v.lower().replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
            if norm in vn:
                candidates.append(v)
        # prefer id-looking strings (snake/kebab, no spaces), highest version
        ids = [c for c in candidates if " " not in c] or candidates
        if not ids:
            raise RuntimeError(
                f"No model matching '{hint}' in your Higgsfield catalog. "
                f"Catalog excerpt: {text[:600]}"
            )
        model = sorted(set(ids))[-1]
        logger.info("Resolved model hint '%s' (%s) -> '%s' (candidates: %s)",
                    hint, kind, model, sorted(set(ids))[:6])
        self._model_cache[cache_key] = model
        return model

    @staticmethod
    def _rank_video_candidates(candidates: list[str]) -> str:
        """Given several video model ids on the same family, pick the one
        most likely to be the newest / most-featured variant. We score each
        id by a set of family-agnostic quality hints (Omni > O3 > 3.0 > Pro
        > higher version numbers), then alphabetical as a stable tiebreak.
        The last item in the sorted list wins."""
        def _score(x: str) -> tuple:
            xl = x.lower()
            # Extract a rough numeric version so kling_3_0 beats kling1_6, etc.
            ver = 0.0
            m = re.search(r"(\d+)[._](\d+)", xl)
            if m:
                try:
                    ver = float(f"{m.group(1)}.{m.group(2)}")
                except ValueError:
                    ver = 0.0
            elif re.search(r"\d+", xl):
                try:
                    ver = float(re.search(r"\d+", xl).group(0))
                except ValueError:
                    ver = 0.0
            return (
                "omni" in xl,
                "o3" in xl,
                "pro" in xl,
                "reference" in xl,
                ver,
                x,
            )
        return sorted(set(candidates), key=_score)[-1]

    async def _fetch_catalog(self, session: ClientSession, hint: str) -> str:
        """Fetch (and cache in-memory for the session) the model catalog text
        from models_explore. Tries search-by-hint first, then a plain list."""
        # models_explore requires {"action": "list"|"search"|"get"|"recommend"}
        text = ""
        attempts = [
            {"action": "search", "query": hint},
            {"action": "search", "q": hint},
            {"action": "list"},
        ]
        last_err = None
        for args in attempts:
            try:
                text = await self._call(session, "models_explore", args, raw=True)
                if text:
                    break
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if not text:
            raise RuntimeError(f"models_explore failed: {last_err}")
        self._catalog_text = text
        return text

    async def _model_roles(self, session: ClientSession, model_id: str) -> set[str]:
        """Return the set of medias[].role values THIS model accepts, per its
        own catalog entry. This is the authoritative source (the generic
        generate_video tool schema says as much: "Role — varies by model;
        inspect the selected model's medias[].roles"). Cached per model.

        For Seedance 2.0 this returns:
            {'start_image', 'end_image', 'image_references',
             'video_references', 'audio_references'}
        which is what makes 'image_references' available for the guest photo
        when the host is on start_image (or vice-versa).
        """
        cached = getattr(self, "_role_cache", None)
        if cached is None:
            self._role_cache = {}
            cached = self._role_cache
        if model_id in cached:
            return cached[model_id]

        # Try the per-model 'get' action first — cleanest, no truncation risk.
        entry = None
        for args in ({"action": "get", "id": model_id},
                     {"action": "get", "model": model_id},
                     {"action": "get", "query": model_id}):
            try:
                text = await self._call(session, "models_explore", args, raw=True)
                entry = self._find_model_entry(text, model_id)
                if entry:
                    break
            except Exception:  # noqa: BLE001
                continue
        # Fallback: hunt in the general catalog dump we already have.
        if entry is None and self._catalog_text:
            entry = self._find_model_entry(self._catalog_text, model_id)
        if entry is None:
            # Last resort: search by the model id and hunt again.
            try:
                text = await self._fetch_catalog(session, model_id)
                entry = self._find_model_entry(text, model_id)
            except Exception:  # noqa: BLE001
                entry = None

        roles: set[str] = set()
        if isinstance(entry, dict):
            medias = entry.get("medias") or entry.get("media") or []
            if isinstance(medias, list):
                for m in medias:
                    if isinstance(m, dict):
                        r = m.get("roles") or m.get("role") or []
                        if isinstance(r, list):
                            roles.update(x for x in r if isinstance(x, str))
                        elif isinstance(r, str):
                            roles.add(r)
            elif isinstance(medias, dict):
                r = medias.get("roles") or medias.get("role") or []
                if isinstance(r, list):
                    roles.update(x for x in r if isinstance(x, str))
        cached[model_id] = roles
        if roles:
            logger.info("Model '%s' declares media roles: %s", model_id, sorted(roles))
        else:
            logger.warning("Could not read media roles for model '%s' — will fall "
                           "back to defaults and let the server auto-coerce.", model_id)
        return roles

    @staticmethod
    def _find_model_entry(text: str, model_id: str) -> dict | None:
        """Locate the JSON object in `text` whose "id" is model_id, with a
        bracket-balanced scan so a truncated outer response doesn't wreck us."""
        if not text:
            return None
        needle = f'"{model_id}"'
        for m in re.finditer(r'"id"\s*:\s*' + re.escape(needle), text):
            i = m.start()
            depth = 0
            start = None
            for j in range(i, -1, -1):
                if text[j] == '}':
                    depth += 1
                elif text[j] == '{':
                    if depth == 0:
                        start = j
                        break
                    depth -= 1
            if start is None:
                continue
            depth = 0
            for k in range(start, len(text)):
                if text[k] == '{':
                    depth += 1
                elif text[k] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:k + 1])
                        except Exception:  # noqa: BLE001
                            break
        return None

    # -- media import ------------------------------------------------------
    _CT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".gif": "image/gif", ".webp": "image/webp", ".wav": "audio/wav",
           ".mp4": "video/mp4"}
    _MEDIA_KIND = {"image/png": "image", "image/jpeg": "image", "image/gif": "image",
                   "image/webp": "image", "audio/wav": "audio", "video/mp4": "video"}

    async def _upload_media_file(self, session: ClientSession, path) -> str:
        """Official Higgsfield upload path (no URL fetch, so no 'fetch failed'):
        media_upload -> get presigned PUT url + media id -> PUT bytes -> media_confirm.
        Returns the confirmed media UUID.

        ROBUSTNESS: results are cached BOTH in-memory (per path) and ON DISK
        (per file-content hash). A file whose bytes were uploaded once is never
        re-uploaded again — across jobs and across server restarts. This is the
        primary defense against Higgsfield's intermittent media_upload errors."""
        from pathlib import Path as _P
        import asyncio as _asyncio
        path = _P(path)

        # Higgsfield's media_upload is picky about formats (audio must be WAV).
        # Its presigned-URL generation has been seen to fail on raw JPEG, so
        # normalize any non-PNG image to PNG first — same idea as forcing WAV for
        # audio. PNG is the format its generated images use and uploads reliably.
        if path.suffix.lower() in (".jpg", ".jpeg", ".webp", ".gif"):
            try:
                from PIL import Image
                png_path = path.with_suffix(".png")
                if not png_path.exists():
                    with Image.open(path) as im:
                        im.convert("RGB").save(png_path, "PNG")
                logger.info("media_upload: converted %s -> %s (PNG) for reliable upload",
                            path.name, png_path.name)
                path = png_path
            except ImportError:
                logger.error("Pillow (PIL) is NOT installed — cannot convert %s to PNG. "
                             "Run: pip install Pillow  (then restart). Uploading original JPEG "
                             "for now, which Higgsfield's media_upload may reject.", path.name)
            except Exception as e:  # noqa: BLE001
                logger.warning("PNG conversion failed for %s (uploading original): %s", path.name, e)

        key = str(path.resolve())
        if key in self._media_cache:
            return self._media_cache[key]

        ext = path.suffix.lower()
        ctype = self._CT.get(ext)
        if not ctype:
            raise RuntimeError(f"Unsupported media type '{ext}' for {path.name}")
        kind = self._MEDIA_KIND.get(ctype, "file")

        # 0) Disk cache by content hash — skip the network entirely if we've
        #    already uploaded these exact bytes before.
        fhash = _file_hash(path)
        disk = _load_upload_cache()
        if fhash in disk and disk[fhash].get("media_id"):
            mid = disk[fhash]["media_id"]
            self._media_cache[key] = mid
            logger.info("media_upload: reusing previously-uploaded %s (cached id %s)", path.name, mid)
            return mid

        # 1) request a presigned upload slot — durable retry with exponential
        #    backoff + jitter. Up to 10 attempts over ~3 minutes.
        # Use a UNIQUE filename each attempt: some servers choke on repeated
        # identical filenames ("host.jpg") sent in quick succession.
        import uuid as _uuid
        unique_name = f"{path.stem}_{_uuid.uuid4().hex[:8]}{path.suffix}"
        up_url = media_id = None
        last_err = None
        MAX_TRIES = 10
        for attempt in range(1, MAX_TRIES + 1):
            try:
                text = await self._call(session, "media_upload",
                                        {"filename": unique_name, "content_type": ctype})
                data = _loads_any(text)
                up_url = _deep_find(data, ("upload_url", "url", "put_url", "presigned_url", "uploadUrl"))
                media_id = _deep_find(data, ("media_id", "id", "uuid", "mediaId"))
                if not up_url or not media_id:
                    files = _deep_find(data, ("files",))
                    if isinstance(files, list) and files:
                        up_url = up_url or files[0].get("upload_url") or files[0].get("url")
                        media_id = media_id or files[0].get("media_id") or files[0].get("id")
                if up_url and media_id:
                    break
                last_err = RuntimeError(f"media_upload gave no upload_url/media_id: {text[:300]}")
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e).lower()
                transient = ("something went wrong" in msg or "try again" in msg
                             or "timeout" in msg or "timed out" in msg
                             or "502" in msg or "503" in msg or "504" in msg
                             or "rate" in msg or "temporarily" in msg)
                if not transient:
                    raise
            if attempt < MAX_TRIES:
                # exponential backoff with jitter: ~2,3,5,8,12,16,20,20,20s
                backoff = min(2 ** min(attempt, 4) + attempt, 20) + random.uniform(0, 2)
                logger.warning("media_upload transient error %d/%d for %s: %s; retry in %.1fs",
                               attempt, MAX_TRIES, path.name, str(last_err)[:120], backoff)
                await _asyncio.sleep(backoff)
        if not up_url or not media_id:
            raise RuntimeError(
                f"media_upload for {path.name} failed after {MAX_TRIES} attempts. "
                f"This is a Higgsfield server-side issue (media_upload). Last error: {last_err}")

        # 2) PUT the raw bytes to the presigned url — retried with jitter
        put_ok = False
        for attempt in range(1, 6):
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    r = await client.put(up_url, content=path.read_bytes(),
                                         headers={"Content-Type": ctype})
                    r.raise_for_status()
                put_ok = True
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                backoff = min(2 * attempt, 10) + random.uniform(0, 1.5)
                logger.warning("media PUT error %d/5 for %s: %s; retry in %.1fs",
                               attempt, path.name, str(e)[:120], backoff)
                await _asyncio.sleep(backoff)
        if not put_ok:
            raise RuntimeError(f"media PUT for {path.name} failed after retries: {last_err}")

        # 3) confirm (best-effort)
        try:
            await self._call(session, "media_confirm",
                            {"type": kind, "media_id": media_id})
        except Exception as e:  # noqa: BLE001
            logger.warning("media_confirm warning (continuing): %s", e)

        # 4) persist to both caches so this file never uploads again
        self._media_cache[key] = media_id
        disk[fhash] = {"media_id": media_id, "name": path.name}
        _save_upload_cache(disk)
        return media_id

    async def register_local_image(self, local_path) -> dict:
        """Upload a local image to Higgsfield and return {"url", "ref"} where ref
        is a reusable media UUID. Used to lock a pre-approved identity image into
        the cache so it is NEVER regenerated."""
        from pathlib import Path as _P
        from . import higgsfield_video as _hv  # reuse the platform uploader

        async def run(session):
            ref = await self._upload_media_file(session, _P(local_path))
            return {"url": str(_P(local_path)), "ref": ref}

        return await self._with_session(run)

    # -- generation --------------------------------------------------------
    async def generate(self, kind: str, *, prompt: str, model_hint: str,
                       image_files: list | None = None,
                       image_refs: list[str] | None = None,
                       audio_files: list | None = None,
                       start_frame_file=None,
                       duration: int | None = None,
                       aspect_ratio: str | None = None,
                       resolution: str | None = None,
                       character_id: str | None = None) -> dict:
        """Returns {"url": <downloadable media url>, "ref": <reusable media id>}.
        image_files -> LOCAL image paths, uploaded via media_upload presigned PUT.
        image_refs  -> already-Higgsfield media ids reused directly (no re-upload).
        audio_files -> LOCAL wav paths, uploaded the same way."""
        async def run(session: ClientSession) -> dict:
            tool = f"generate_{kind}"
            if tool not in self._schemas:
                raise RuntimeError(f"Tool {tool} not on server; have: {list(self._schemas)[:20]}")

            # First, materialise every conditioning image into an ordered list of
            # Higgsfield media ids. refs (already on Higgsfield) come first, then
            # any local files (uploaded once, cached across runs).
            ordered_img_values: list[str] = []
            for ref in image_refs or []:               # already on Higgsfield — reference directly
                ordered_img_values.append(ref)
            for f in image_files or []:                # local file -> presigned upload (once)
                ordered_img_values.append(await self._upload_media_file(session, f))

            model = await self._resolve_model(session, model_hint, kind)
            is_video = kind == "video"

            # Fetch the role vocabulary this specific model advertises in the
            # catalog. The top-level generate_video schema is generic
            # ("Role — varies by model; inspect the selected model's
            # medias[].roles"), so the truth lives in the catalog entry. For
            # Seedance 2.0 this returns roles including 'image_references' —
            # the plural role that lets host + guest ride into the same clip.
            allowed_roles = await self._model_roles(session, model)

            def pick(*candidates: str) -> str:
                for c in candidates:
                    if not allowed_roles or c in allowed_roles:
                        return c
                # Nothing matched (unusual). Return the first candidate and
                # let the server auto-coerce or error clearly.
                return candidates[0]

            # Primary conditioning image role — the FIRST frame the model
            # animates from and the identity that lip-syncs to the audio.
            # Different families use different names for this slot; the
            # candidate list covers Seedance ('start_image'), most Kling
            # variants ('start_image' or 'first_frame' or 'image'), and any
            # server that expects a generic 'image'.
            if is_video:
                primary_role = pick("start_image", "first_frame", "start_frame", "image")
            else:
                # image-generation (gpt_image_2 edit etc.) uses 'image' first
                primary_role = pick("image", "start_image")

            # Secondary (reference) image role — this is what carries the
            # listener's face into the same clip so a reaction/nod can render.
            # Seedance 2.0's catalog entry calls it 'image_references' (plural,
            # repeatable). Kling variants have historically used a mix of
            # 'reference_images', 'character_references', 'identity_references'
            # and — on the newer Omni tier — an '@' element system that
            # surfaces here as either 'element' or 'element_references'. Try
            # the plural / most-recent names first, then fall back through
            # older/simpler ones so the server can auto-coerce if none
            # matches exactly.
            if is_video:
                secondary_role = pick(
                    "image_references", "character_references",
                    "identity_references", "reference_images",
                    "element_references", "reference_image",
                    "character_reference", "identity_reference",
                    "reference", "image_reference", "element", "image",
                )
            else:
                secondary_role = pick(
                    "image_references", "reference_images", "image",
                    "reference_image", "reference",
                )

            # Audio role — Seedance calls it 'audio_references'; Kling may
            # use 'audio_reference' or 'voice_reference' (for voice cloning)
            # or just 'audio'. The server auto-coerces plain 'audio' in most
            # cases; we still try to be exact when possible.
            audio_role = pick(
                "audio_references", "audio_reference",
                "voice_references", "voice_reference", "audio",
            )

            medias: list[dict] = []
            if start_frame_file is not None:
                # explicit continuity frame takes the start slot; all identity
                # images become secondary references
                start_id = await self._upload_media_file(session, start_frame_file)
                medias.append({"value": start_id, "role": "start_image"})
                for v in ordered_img_values:
                    medias.append({"value": v, "role": secondary_role})
            elif is_video:
                # FIRST image = primary subject (start_image / lip-sync anchor).
                # EVERY additional image = an identity reference under the
                # plural reference role — Seedance and Kling both accept
                # multiple items sharing this role, which is exactly what
                # lets both host and guest ride into the same clip.
                for i, v in enumerate(ordered_img_values):
                    medias.append({"value": v,
                                   "role": primary_role if i == 0 else secondary_role})
            else:
                # non-video: send everything with the primary role
                # (e.g. gpt_image_2 edit accepts multiple 'image' entries).
                for v in ordered_img_values:
                    medias.append({"value": v, "role": primary_role})
            for f in audio_files or []:
                medias.append({"value": await self._upload_media_file(session, f),
                               "role": audio_role})

            # Loud, structured logging of the outgoing plan so you can see in
            # the console exactly which images (and which roles) go to
            # Higgsfield for every scene. If Higgsfield's UI ever shows fewer
            # inputs than this line lists, the payload was correct and the
            # discrepancy is server-side rendering — not a plumbing bug.
            role_summary = [(m["role"], str(m["value"])[:12] + "…") for m in medias]
            logger.info(
                "generate(%s) plan: model=%s, images=%d, medias=%s, "
                "model_roles=%s",
                kind, model, len(ordered_img_values), role_summary,
                sorted(allowed_roles) if allowed_roles else "unknown",
            )

            inner: dict[str, Any] = {"model": model, "prompt": prompt}
            if aspect_ratio:
                inner["aspect_ratio"] = aspect_ratio
            if duration is not None and kind == "video":
                inner["duration"] = int(max(4, min(15, duration)))  # seedance range
            if resolution:
                # model-specific param names: seedance/gpt use params.resolution
                inner["resolution"] = resolution
            if medias:
                inner["medias"] = medias
            if character_id:
                inner["character_id"] = character_id

            # Preflight cost (zero spend) and enforce a per-clip ceiling
            cost = await self._get_cost(session, tool, inner)
            if cost is not None:
                logger.info("%s preflight cost: %s credits", tool, cost)
                job = self._job
                if job is not None:
                    job["cost_estimate"] = job.get("cost_estimate", 0) + cost
                ceiling = get_settings().hf_max_credits_per_clip
                if kind == "video" and ceiling and cost > ceiling:
                    raise RuntimeError(
                        f"Seedance clip would cost {cost} credits, over the "
                        f"HF_MAX_CREDITS_PER_CLIP ceiling of {ceiling}. Aborting before spend."
                    )

            text = await self._call(session, tool, inner)

            # Higgsfield may intercept the prompt and recommend a PRESET instead of
            # generating (returns a preset_recommendation notice, NO job created).
            # Decline the preset and resubmit to force literal generation. Loop a
            # few times in case it suggests multiple presets.
            declined: list[str] = []
            for _ in range(4):
                if _has_real_job(text):
                    break
                preset_id = _detect_preset_recommendation(text)
                if not preset_id:
                    break
                logger.info("%s: declining preset recommendation %s and forcing literal generation",
                            tool, preset_id)
                declined.append(preset_id)
                retry_inner = dict(inner)
                # the schema accepts a single declined_preset_id; pass the latest
                retry_inner["declined_preset_id"] = preset_id
                text = await self._call(session, tool, retry_inner)

            job_id = _find_job_id(text)
            logger.info("%s submit -> parsed job_id=%s | raw response head: %s",
                        tool, job_id, text[:300].replace(chr(10), " "))

            # VIDEO: never trust an inline URL in the submit response — Higgsfield
            # returns a preset/sample clip there (job_set_chain_preset/...). Always
            # poll the job to get the REAL rendered output.
            if kind == "video":
                if not job_id:
                    raise RuntimeError(f"{tool}: no job id to poll: {text[:400]}")
                url = await self._poll(session, job_id, prefer="video")
                return {"url": url, "ref": job_id}

            # IMAGE: inline URL is fine; fall back to polling if absent.
            inline = self._real_media_url(text, prefer="image")
            if inline:
                return {"url": inline, "ref": job_id}
            if not job_id:
                raise RuntimeError(f"{tool}: no media URL or job id: {text[:400]}")
            url = await self._poll(session, job_id, prefer="image")
            return {"url": url, "ref": job_id}

        return await self._with_session(run)

    @staticmethod
    def _real_media_url(text: str, prefer: str = "any") -> str | None:
        """Return a finished-output media URL, ignoring preset/sample decoys and
        input-media echoes. prefer='video' picks .mp4/.webm/.mov; 'image' picks
        image types; 'any' returns the first acceptable url."""
        def bad(u: str) -> bool:
            ul = u.lower()
            return ("job_set_chain_preset" in ul or "/preset" in ul or "sample" in ul
                    # input media is echoed back in params; outputs live on cdn.higgsfield.ai
                    or "d2ol7oe51mr4n9.cloudfront.net" in ul)
        vids, imgs, other = [], [], []
        for m in MEDIA_URL_RE.finditer(text):
            u = m.group(0)
            if bad(u):
                continue
            ul = u.lower()
            if ul.split("?")[0].endswith((".mp4", ".webm", ".mov")):
                vids.append(u)
            elif ul.split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                imgs.append(u)
            else:
                other.append(u)
        if prefer == "video":
            return (vids or other or imgs or [None])[0]
        if prefer == "image":
            return (imgs or other or vids or [None])[0]
        return (vids or imgs or other or [None])[0]

    async def _poll(self, session: ClientSession, job_id: str, prefer: str = "any") -> str:
        """Poll job_status using the REAL schema: param is 'jobId' (camelCase),
        and sync:true makes the server wait up to ~25s and return on terminal
        state. Transient server errors during polling are retried (the job keeps
        rendering on Higgsfield's side regardless of a flaky status call)."""
        waited = 0
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 15
        while waited < POLL_TIMEOUT_S:
            try:
                text = await self._call(session, "job_status", {"jobId": job_id, "sync": True})
                consecutive_errors = 0
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                # A real generation failure can surface as an error payload — only
                # treat explicit job-failure statuses as fatal; everything else is
                # a transient status-endpoint hiccup ("Something went wrong", 5xx).
                if re.search(r'"status"\s*:\s*"(failed|nsfw|rejected|canceled|cancelled)"', msg, re.I):
                    raise
                consecutive_errors += 1
                logger.warning("job_status transient error %d/%d for %s: %s",
                               consecutive_errors, MAX_CONSECUTIVE_ERRORS, job_id, msg[:160])
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise RuntimeError(
                        f"job_status kept failing for {job_id} after "
                        f"{MAX_CONSECUTIVE_ERRORS} tries: {msg[:200]}")
                await asyncio.sleep(12)
                waited += 12
                continue

            failed = re.search(r'"status"\s*:\s*"(failed|nsfw|error|canceled|cancelled|rejected)"', text, re.I)
            if failed:
                raise RuntimeError(f"Higgsfield generation failed: {text[:500]}")

            done = re.search(r'"status"\s*:\s*"(completed|succeeded|success|done|ready|finished)"', text, re.I)
            if done:
                url = self._real_media_url(text, prefer)
                if url:
                    return url
                for reveal in ("job_display", "reveal_generation", "show_generations"):
                    if reveal in self._schemas:
                        try:
                            t2 = await self._call(session, reveal, {"jobId": job_id})
                            url = self._real_media_url(t2, prefer)
                            if url:
                                return url
                        except Exception:  # noqa: BLE001
                            continue
                raise RuntimeError(f"Job {job_id} completed but no real media URL: {text[:500]}")

            # sync:true already waited ~25s server-side; brief client gap then retry
            await asyncio.sleep(3)
            waited += 28
        raise TimeoutError(f"Generation {job_id} timed out after {POLL_TIMEOUT_S}s")