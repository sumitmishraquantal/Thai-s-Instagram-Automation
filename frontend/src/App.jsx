


// import React, { useEffect, useMemo, useRef, useState } from "react";
// import { api } from "./lib/api.js";

// const CATEGORIES = [
//   "Mental Health", "Mental Wellness", "Wellbeing", "Anxiety",
//   "Peace & Mindfulness", "Addiction", "Trauma Recovery", "Recovery Centers",
// ];

// const T = {
//   bg: "#101418", panel: "#181E24", panel2: "#1F2730", line: "#2B3640",
//   text: "#E8EDF1", dim: "#8A98A5", amber: "#F2A33C", amberSoft: "#3A2D17",
//   teal: "#5BC8AF", red: "#E0654F",
// };

// export default function App() {
//   const [category, setCategory] = useState(CATEGORIES[0]);
//   const [trending, setTrending] = useState([]);
//   const [loadingTopics, setLoadingTopics] = useState(false);

//   const [scriptInput, setScriptInput] = useState("");
//   const [script, setScript] = useState(null);
//   const [generating, setGenerating] = useState(false);

//   const [voices, setVoices] = useState([]);
//   const [hostVoice, setHostVoice] = useState("");
//   const [guestVoice, setGuestVoice] = useState("");

//   const [synthesizing, setSynthesizing] = useState(false);
//   const [rendering, setRendering] = useState(false);
//   const [renderResult, setRenderResult] = useState(null);
//   const [planning, setPlanning] = useState(false);
//   const [blueprint, setBlueprint] = useState(null);
//   const [videoJob, setVideoJob] = useState(null);
//   const [chars, setChars] = useState({ host: null, guest: null });
//   const [startingJob, setStartingJob] = useState(false);
//   const [playingIdx, setPlayingIdx] = useState(-1);
//   const [error, setError] = useState("");
//   const audioRef = useRef(null);
//   const stopRef = useRef(false);

//   useEffect(() => { api.characters().then(setChars).catch(() => {}); }, []);

//   useEffect(() => {
//     api.voices()
//       .then((v) => {
//         setVoices(v);
//         if (v[0]) setHostVoice(v[0].voice_id);
//         if (v[1]) setGuestVoice(v[1].voice_id);
//         else if (v[0]) setGuestVoice(v[0].voice_id);
//       })
//       .catch(() => setError("Couldn't load ElevenLabs voices — check the backend and your XI key."));
//   }, []);

//   const totalSec = useMemo(
//     () => (script ? Math.round(script.lines.reduce((a, l) => a + (l.seconds || 0), 0)) : 0),
//     [script]
//   );

//   // Merge consecutive lines from the same speaker into one continuous block —
//   // one box in the UI, one TTS request (better prosody, natural flow).
//   const groupedLines = useMemo(() => {
//     if (!script) return [];
//     const groups = [];
//     for (const l of script.lines) {
//       const last = groups[groups.length - 1];
//       if (last && last.speaker === l.speaker) {
//         last.text += " " + l.text;
//         last.seconds += l.seconds;
//       } else {
//         groups.push({ speaker: l.speaker, text: l.text, emotion: l.emotion, seconds: l.seconds });
//       }
//     }
//     return groups;
//   }, [script]);
//   const inBudget = totalSec >= 45 && totalSec <= 50;

//   async function fetchTrending() {
//     setLoadingTopics(true); setError("");
//     try {
//       const res = await api.research(category);
//       setTrending(res.topics);
//     } catch (e) { setError(e.message); }
//     setLoadingTopics(false);
//   }

//   async function generateScript(seedTopic) {
//     setGenerating(true); setError(""); setPlayingIdx(-1);
//     setRenderResult(null); setBlueprint(null);
//     try {
//       const pkg = await api.script({
//         category,
//         seed_topic: seedTopic || null,
//         user_draft: scriptInput.trim() || null,
//       });
//       setScript(pkg);
//     } catch (e) { setError(e.message); }
//     setGenerating(false);
//   }

//   async function previewVoices() {
//     if (!script || !hostVoice || !guestVoice) return;
//     setSynthesizing(true); setError(""); stopRef.current = false;
//     try {
//       const res = await api.ttsPreview(groupedLines, hostVoice, guestVoice);
//       setSynthesizing(false);
//       for (const clip of res.clips) {
//         if (stopRef.current) break;
//         setPlayingIdx(clip.index);
//         await playBase64(clip.audio_base64, clip.mime_type);
//       }
//       setPlayingIdx(-1);
//     } catch (e) {
//       setError(e.message);
//       setSynthesizing(false);
//       setPlayingIdx(-1);
//     }
//   }

//   async function renderFinal() {
//     if (!script) return;
//     setRendering(true); setError("");
//     try {
//       const res = await api.render(script.title, script.lines, hostVoice, guestVoice);
//       setRenderResult(res);
//     } catch (e) { setError(e.message); }
//     setRendering(false);
//   }

//   async function planScenes() {
//     if (!script) return;
//     setPlanning(true); setError("");
//     try {
//       const segs = renderResult?.segments?.map((s) => ({
//         index: s.index, start_second: s.start_second, end_second: s.end_second, text: s.text,
//       })) || null;
//       const bp = await api.scenePlan(script.title, script.lines, segs);
//       setBlueprint(bp);
//     } catch (e) { setError(e.message); }
//     setPlanning(false);
//   }

//   async function uploadChar(role, file) {
//     if (!file) return;
//     setError("");
//     try { setChars(await api.uploadCharacter(role, file)); }
//     catch (e) { setError(e.message); }
//   }

//   async function startVideoJob({ retry = false, forceRegen = false } = {}) {
//     if (!renderResult || !blueprint) return;
//     setStartingJob(true); setError("");
//     try {
//       const { job_id } = retry
//         ? await api.retryVideos(renderResult.render_id, blueprint, forceRegen)
//         : await api.generateVideos(renderResult.render_id, blueprint);
//       const poll = async () => {
//         try {
//           const j = await api.videoJob(job_id);
//           setVideoJob(j);
//           if (j.status !== "completed" && j.status !== "failed") setTimeout(poll, 5000);
//         } catch { setTimeout(poll, 8000); }
//       };
//       poll();
//     } catch (e) { setError(e.message); }
//     setStartingJob(false);
//   }

//   function playBase64(b64, mime) {
//     return new Promise((resolve) => {
//       const audio = new Audio(`data:${mime};base64,${b64}`);
//       audioRef.current = audio;
//       audio.onended = resolve;
//       audio.onerror = resolve;
//       audio.play().catch(resolve);
//     });
//   }

//   function stopPlayback() {
//     stopRef.current = true;
//     if (audioRef.current) audioRef.current.pause();
//     setPlayingIdx(-1);
//   }

//   const S = {
//     app: { minHeight: "100vh", background: T.bg, color: T.text, fontFamily: "'Sora', 'Segoe UI', sans-serif", padding: "28px 20px 60px" },
//     wrap: { maxWidth: 880, margin: "0 auto" },
//     eyebrow: { color: T.amber, letterSpacing: "0.25em", fontSize: 11, fontWeight: 700 },
//     h1: { fontSize: 30, margin: "8px 0 4px", fontWeight: 700 },
//     sub: { color: T.dim, fontSize: 14, marginBottom: 28 },
//     panel: { background: T.panel, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, marginBottom: 18 },
//     label: { fontSize: 12, color: T.dim, letterSpacing: "0.08em", textTransform: "uppercase", display: "block", marginBottom: 8, fontWeight: 600 },
//     select: { width: "100%", background: T.panel2, color: T.text, border: `1px solid ${T.line}`, borderRadius: 8, padding: "10px 12px", fontSize: 14 },
//     ta: { width: "100%", minHeight: 110, background: T.panel2, color: T.text, border: `1px solid ${T.line}`, borderRadius: 8, padding: 12, fontSize: 14, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" },
//     btn: (primary, disabled) => ({
//       background: primary ? T.amber : "transparent", color: primary ? "#1A1205" : T.amber,
//       border: `1px solid ${T.amber}`, borderRadius: 8, padding: "10px 18px",
//       fontWeight: 700, fontSize: 14, cursor: disabled ? "wait" : "pointer", opacity: disabled ? 0.6 : 1,
//     }),
//     chip: { background: T.amberSoft, border: `1px solid ${T.amber}44`, color: T.text, borderRadius: 10, padding: "10px 12px", cursor: "pointer", textAlign: "left", fontSize: 13, flex: "1 1 240px", fontFamily: "inherit" },
//     row: { display: "flex", gap: 12, flexWrap: "wrap" },
//     lineCard: (host, active) => ({
//       borderLeft: `3px solid ${host ? T.amber : T.teal}`,
//       background: active ? "#243140" : T.panel2,
//       borderRadius: 8, padding: "10px 14px", marginBottom: 10,
//       transition: "background 0.3s",
//     }),
//   };

//   return (
//     <div style={S.app}>
//       <div style={S.wrap}>
//         <div style={S.eyebrow}>● REC&nbsp;&nbsp;REEL STUDIO</div>
//         <h1 style={S.h1}>Script &amp; Voice Mapping</h1>
//         <div style={S.sub}>Research → Script (45–50s) → ElevenLabs voice preview</div>

//         {error && (
//           <div style={{ ...S.panel, borderColor: T.red, color: T.red, fontSize: 13 }}>{error}</div>
//         )}

//         <div style={S.panel}>
//           <label style={S.label}>1 · Topic research agent</label>
//           <div style={S.row}>
//             <select style={{ ...S.select, flex: 1, minWidth: 220 }} value={category} onChange={(e) => setCategory(e.target.value)}>
//               {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
//             </select>
//             <button style={S.btn(false, loadingTopics)} onClick={fetchTrending} disabled={loadingTopics}>
//               {loadingTopics ? "Searching the web…" : "Find trending topics"}
//             </button>
//           </div>
//           {trending.length > 0 && (
//             <div style={{ ...S.row, marginTop: 14 }}>
//               {trending.map((t, i) => (
//                 <button key={i} style={S.chip} onClick={() => generateScript(`${t.topic} — ${t.angle}`)}>
//                   <div style={{ fontWeight: 700 }}>{t.topic}</div>
//                   <div style={{ color: T.dim, marginTop: 4 }}>{t.angle}</div>
//                 </button>
//               ))}
//             </div>
//           )}
//         </div>

//         <div style={S.panel}>
//           <label style={S.label}>2 · Script input — or write your own idea</label>
//           <textarea
//             style={S.ta}
//             placeholder="Paste a draft script or describe the reel you want… (leave empty to auto-generate from the category above)"
//             value={scriptInput}
//             onChange={(e) => setScriptInput(e.target.value)}
//           />
//           <div style={{ marginTop: 12 }}>
//             <button style={S.btn(true, generating)} onClick={() => generateScript(null)} disabled={generating}>
//               {generating ? "Writing script…" : "Generate 45–50s reel script"}
//             </button>
//           </div>
//         </div>

//         <div style={S.panel}>
//           <label style={S.label}>3 · Voice mapping (ElevenLabs — live from your account)</label>
//           <div style={S.row}>
//             <div style={{ flex: 1, minWidth: 220 }}>
//               <label style={{ ...S.label, marginBottom: 6 }}>Host voice</label>
//               <select style={S.select} value={hostVoice} onChange={(e) => setHostVoice(e.target.value)}>
//                 {voices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
//               </select>
//             </div>
//             <div style={{ flex: 1, minWidth: 220 }}>
//               <label style={{ ...S.label, marginBottom: 6 }}>Guest voice</label>
//               <select style={S.select} value={guestVoice} onChange={(e) => setGuestVoice(e.target.value)}>
//                 {voices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
//               </select>
//             </div>
//           </div>
//         </div>

//         {script && (
//           <div style={S.panel}>
//             <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
//               <label style={S.label}>4 · Preview — {script.title}</label>
//               <div style={{ fontSize: 13, color: inBudget ? T.teal : T.red, fontWeight: 700 }}>
//                 ⏱ {totalSec}s {inBudget ? "· within budget" : "· outside 45–50s budget"}
//               </div>
//             </div>

//             <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", margin: "6px 0 16px", border: `1px solid ${T.line}` }}>
//               {groupedLines.map((l, i) => (
//                 <div key={i} title={`${l.speaker} · ${Math.round(l.seconds)}s`}
//                   style={{ width: `${(l.seconds / Math.max(totalSec, 1)) * 100}%`, background: l.speaker === "HOST" ? T.amber : T.teal }} />
//               ))}
//             </div>

//             {groupedLines.map((l, i) => (
//               <div key={i} style={S.lineCard(l.speaker === "HOST", playingIdx === i)}>
//                 <div style={{ fontSize: 11, color: T.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
//                   {playingIdx === i ? "▶ " : ""}{l.speaker} · <em>{l.emotion}</em> · {Math.round(l.seconds)}s
//                 </div>
//                 <div style={{ fontSize: 14, lineHeight: 1.5 }}>{l.text}</div>
//               </div>
//             ))}

//             <div style={{ marginTop: 14, display: "flex", gap: 12, flexWrap: "wrap" }}>
//               <button style={S.btn(true, synthesizing)} onClick={previewVoices} disabled={synthesizing}>
//                 {synthesizing ? "Synthesizing voices…" : "▶ Preview with voices"}
//               </button>
//               {playingIdx >= 0 && (
//                 <button style={S.btn(false, false)} onClick={stopPlayback}>■ Stop</button>
//               )}
//               <button style={S.btn(false, generating)} onClick={() => generateScript(null)} disabled={generating}>
//                 ↻ Regenerate
//               </button>
//             </div>
//           </div>
//         )}

//         {script && (
//           <div style={S.panel}>
//             <label style={S.label}>5 · Render &amp; export</label>
//             <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
//               <button style={S.btn(true, rendering)} onClick={renderFinal} disabled={rendering}>
//                 {rendering ? "Rendering audio…" : "⬇ Render final audio + subtitles"}
//               </button>
//               <button style={S.btn(false, planning)} onClick={planScenes} disabled={planning}>
//                 {planning ? "Planning scenes…" : "🎬 Generate scene blueprint"}
//               </button>
//             </div>

//             {renderResult && (
//               <div style={{ marginTop: 16 }}>
//                 <audio controls src={renderResult.audio_url} style={{ width: "100%" }} />
//                 <div style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap", fontSize: 13 }}>
//                   <a href={renderResult.audio_url} download style={{ color: T.amber }}>Download MP3</a>
//                   <a href={renderResult.srt_url} download style={{ color: T.amber }}>Download SRT subtitles</a>
//                   <a href={renderResult.script_url} download style={{ color: T.amber }}>Download Script JSON</a>
//                   <span style={{ color: T.dim }}>Final length: {renderResult.total_seconds}s</span>
//                 </div>

//                 {renderResult.segments?.length > 0 && (
//                   <div style={{ marginTop: 16 }}>
//                     <div style={{ fontSize: 12, color: T.dim, letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600, marginBottom: 8 }}>
//                       Video segments (word-safe cuts · one clip per Higgsfield generation)
//                     </div>
//                     {renderResult.segments.map((s) => (
//                       <div key={s.index} style={{ background: T.panel2, border: `1px solid ${T.line}`, borderRadius: 8, padding: "10px 14px", marginBottom: 8 }}>
//                         <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
//                           <span style={{ fontSize: 12, color: T.teal, fontWeight: 700 }}>
//                             SEGMENT {s.index} · {s.start_second}–{s.end_second}s ({s.duration}s)
//                           </span>
//                           <a href={s.audio_url} download style={{ color: T.amber, fontSize: 12 }}>Download clip audio</a>
//                         </div>
//                         <audio controls src={s.audio_url} style={{ width: "100%", height: 32 }} />
//                         <div style={{ fontSize: 12, color: T.dim, marginTop: 6, lineHeight: 1.5 }}>{s.text}</div>
//                       </div>
//                     ))}
//                   </div>
//                 )}
//               </div>
//             )}

//             {blueprint && (
//               <div style={{ marginTop: 16 }}>
//                 {blueprint.scenes.map((s) => (
//                   <div key={s.scene_number} style={{ background: T.panel2, border: `1px solid ${T.line}`, borderRadius: 8, padding: "12px 14px", marginBottom: 10 }}>
//                     <div style={{ fontSize: 12, color: T.amber, fontWeight: 700, marginBottom: 6 }}>
//                       SCENE {s.scene_number} · {s.start_second}–{s.end_second}s · {s.speaker_on_camera} on camera
//                     </div>
//                     <div style={{ fontSize: 13, lineHeight: 1.7, color: T.text }}>
//                       <span style={{ color: T.dim }}>Action:</span> {s.character_action}<br />
//                       <span style={{ color: T.dim }}>Expression:</span> {s.facial_expression}<br />
//                       <span style={{ color: T.dim }}>Camera:</span> {s.camera_movement}<br />
//                       <span style={{ color: T.dim }}>Background:</span> {s.background_environment}
//                     </div>
//                   </div>
//                 ))}
//               </div>
//             )}
//           </div>
//         )}

//         {blueprint && renderResult && (
//           <div style={S.panel}>
//             <label style={S.label}>6 · Video generation (Seedance 2.0 · 9:16 · 720p)</label>
//             {!videoJob && (
//               <button style={S.btn(true, startingJob)} onClick={startVideoJob} disabled={startingJob}>
//                 {startingJob ? "Starting…" : "🎥 Generate all scene videos"}
//               </button>
//             )}

//             {videoJob && (
//               <div>
//                 <div style={{ fontSize: 13, marginBottom: 10 }}>
//                   <span style={{ color: videoJob.status === "failed" ? T.red : videoJob.status === "completed" ? T.teal : T.amber, fontWeight: 700 }}>
//                     {videoJob.status.toUpperCase()}
//                   </span>
//                   <span style={{ color: T.dim }}> — {videoJob.step}</span>
//                 </div>
//                 {videoJob.error && <div style={{ color: T.red, fontSize: 13, marginBottom: 10 }}>{videoJob.error}</div>}

//                 {Object.keys(videoJob.images || {}).length > 0 && (
//                   <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
//                     {Object.entries(videoJob.images).map(([name, url]) => (
//                       <div key={name} style={{ textAlign: "center" }}>
//                         <img src={url} alt={name} style={{ width: 90, borderRadius: 8, border: `1px solid ${T.line}` }} />
//                         <div style={{ fontSize: 11, color: T.dim, marginTop: 4 }}>{name}</div>
//                       </div>
//                     ))}
//                   </div>
//                 )}

//                 {(videoJob.scenes || []).map((sc) => (
//                   <div key={sc.scene_number} style={{ marginBottom: 12 }}>
//                     <div style={{ fontSize: 12, color: T.teal, fontWeight: 700, marginBottom: 4 }}>SCENE {sc.scene_number}</div>
//                     <video controls src={sc.video_url} style={{ width: 200, borderRadius: 8, border: `1px solid ${T.line}` }} />
//                   </div>
//                 ))}

//                 {videoJob.merged_url && (
//                   <div style={{ marginTop: 16 }}>
//                     <div style={{ fontSize: 12, color: T.amber, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 8 }}>FINAL MERGED REEL</div>
//                     <video controls src={videoJob.merged_url} style={{ width: 260, borderRadius: 10, border: `2px solid ${T.amber}` }} />
//                     <div style={{ marginTop: 8 }}>
//                       <a href={videoJob.merged_url} download style={{ color: T.amber, fontSize: 13 }}>Download merged reel (MP4)</a>
//                     </div>
//                   </div>
//                 )}

//                 {videoJob.status === "failed" && (
//                   <>
//                     <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
//                       <button style={S.btn(false, false)} disabled={startingJob}
//                         onClick={() => startVideoJob({ retry: true })}>
//                         ↻ Retry (resume — keeps finished scenes)
//                       </button>
//                       <button style={S.btn(false, false)} disabled={startingJob}
//                         onClick={() => startVideoJob({ retry: true, forceRegen: true })}>
//                         ⟳ Regenerate all scenes
//                       </button>
//                     </div>
//                     <div style={{ fontSize: 12, color: "#888", marginTop: 6 }}>
//                       Retry keeps already-generated scenes (same script, 0 extra credits for those) and only continues the missing ones.
//                     </div>
//                   </>
//                 )}
//               </div>
//             )}
//           </div>
//         )}
//       </div>
//     </div>
//   );
// }



import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./lib/api.js";

const CATEGORIES = [
  "Mental Health", "Mental Wellness", "Wellbeing", "Anxiety",
  "Peace & Mindfulness", "Addiction", "Trauma Recovery", "Recovery Centers",
];

const T = {
  bg: "#101418", panel: "#181E24", panel2: "#1F2730", line: "#2B3640",
  text: "#E8EDF1", dim: "#8A98A5", amber: "#F2A33C", amberSoft: "#3A2D17",
  teal: "#5BC8AF", red: "#E0654F",
};

export default function App() {
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [trending, setTrending] = useState([]);
  const [loadingTopics, setLoadingTopics] = useState(false);

  const [scriptInput, setScriptInput] = useState("");
  const [script, setScript] = useState(null);
  const [generating, setGenerating] = useState(false);

  const [voices, setVoices] = useState([]);
  const [hostVoice, setHostVoice] = useState("");
  const [guestVoice, setGuestVoice] = useState("");

  const [synthesizing, setSynthesizing] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderResult, setRenderResult] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [blueprint, setBlueprint] = useState(null);
  const [videoJob, setVideoJob] = useState(null);
  const [chars, setChars] = useState({ host: null, guest: null });
  const [startingJob, setStartingJob] = useState(false);
  const [playingIdx, setPlayingIdx] = useState(-1);
  const [error, setError] = useState("");
  const audioRef = useRef(null);
  const stopRef = useRef(false);

  useEffect(() => { api.characters().then(setChars).catch(() => {}); }, []);

  useEffect(() => {
    api.voices()
      .then((v) => {
        setVoices(v);
        if (v[0]) setHostVoice(v[0].voice_id);
        if (v[1]) setGuestVoice(v[1].voice_id);
        else if (v[0]) setGuestVoice(v[0].voice_id);
      })
      .catch(() => setError("Couldn't load ElevenLabs voices — check the backend and your XI key."));
  }, []);

  const totalSec = useMemo(
    () => (script ? Math.round(script.lines.reduce((a, l) => a + (l.seconds || 0), 0)) : 0),
    [script]
  );

  // Merge consecutive lines from the same speaker into one continuous block —
  // one box in the UI, one TTS request (better prosody, natural flow).
  const groupedLines = useMemo(() => {
    if (!script) return [];
    const groups = [];
    for (const l of script.lines) {
      const last = groups[groups.length - 1];
      if (last && last.speaker === l.speaker) {
        last.text += " " + l.text;
        last.seconds += l.seconds;
      } else {
        groups.push({ speaker: l.speaker, text: l.text, emotion: l.emotion, seconds: l.seconds });
      }
    }
    return groups;
  }, [script]);
  const inBudget = totalSec >= 45 && totalSec <= 50;

  async function fetchTrending() {
    setLoadingTopics(true); setError("");
    try {
      const res = await api.research(category);
      setTrending(res.topics);
    } catch (e) { setError(e.message); }
    setLoadingTopics(false);
  }

  async function generateScript(seedTopic) {
    setGenerating(true); setError(""); setPlayingIdx(-1);
    setRenderResult(null); setBlueprint(null);
    try {
      const pkg = await api.script({
        category,
        seed_topic: seedTopic || null,
        user_draft: scriptInput.trim() || null,
      });
      setScript(pkg);
    } catch (e) { setError(e.message); }
    setGenerating(false);
  }

  async function previewVoices() {
    if (!script || !hostVoice || !guestVoice) return;
    setSynthesizing(true); setError(""); stopRef.current = false;
    try {
      const res = await api.ttsPreview(groupedLines, hostVoice, guestVoice);
      setSynthesizing(false);
      for (const clip of res.clips) {
        if (stopRef.current) break;
        setPlayingIdx(clip.index);
        await playBase64(clip.audio_base64, clip.mime_type);
      }
      setPlayingIdx(-1);
    } catch (e) {
      setError(e.message);
      setSynthesizing(false);
      setPlayingIdx(-1);
    }
  }

  async function renderFinal() {
    if (!script) return;
    setRendering(true); setError("");
    try {
      const res = await api.render(script.title, script.lines, hostVoice, guestVoice);
      setRenderResult(res);
    } catch (e) { setError(e.message); }
    setRendering(false);
  }

  async function planScenes() {
    if (!script) return;
    setPlanning(true); setError("");
    try {
      const segs = renderResult?.segments?.map((s) => ({
        index: s.index, start_second: s.start_second, end_second: s.end_second, text: s.text,
      })) || null;
      const bp = await api.scenePlan(script.title, script.lines, segs);
      setBlueprint(bp);
    } catch (e) { setError(e.message); }
    setPlanning(false);
  }

  async function uploadChar(role, file) {
    if (!file) return;
    setError("");
    try { setChars(await api.uploadCharacter(role, file)); }
    catch (e) { setError(e.message); }
  }

  async function startVideoJob({ retry = false, forceRegen = false } = {}) {
    if (!renderResult || !blueprint) return;
    setStartingJob(true); setError("");
    try {
      const { job_id } = retry
        ? await api.retryVideos(renderResult.render_id, blueprint, forceRegen)
        : await api.generateVideos(renderResult.render_id, blueprint);
      const poll = async () => {
        try {
          const j = await api.videoJob(job_id);
          setVideoJob(j);
          if (j.status !== "completed" && j.status !== "failed") setTimeout(poll, 5000);
        } catch { setTimeout(poll, 8000); }
      };
      poll();
    } catch (e) { setError(e.message); }
    setStartingJob(false);
  }

  function playBase64(b64, mime) {
    return new Promise((resolve) => {
      const audio = new Audio(`data:${mime};base64,${b64}`);
      audioRef.current = audio;
      audio.onended = resolve;
      audio.onerror = resolve;
      audio.play().catch(resolve);
    });
  }

  function stopPlayback() {
    stopRef.current = true;
    if (audioRef.current) audioRef.current.pause();
    setPlayingIdx(-1);
  }

  const S = {
    app: { minHeight: "100vh", background: T.bg, color: T.text, fontFamily: "'Sora', 'Segoe UI', sans-serif", padding: "28px 20px 60px" },
    wrap: { maxWidth: 880, margin: "0 auto" },
    eyebrow: { color: T.amber, letterSpacing: "0.25em", fontSize: 11, fontWeight: 700 },
    h1: { fontSize: 30, margin: "8px 0 4px", fontWeight: 700 },
    sub: { color: T.dim, fontSize: 14, marginBottom: 28 },
    panel: { background: T.panel, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, marginBottom: 18 },
    label: { fontSize: 12, color: T.dim, letterSpacing: "0.08em", textTransform: "uppercase", display: "block", marginBottom: 8, fontWeight: 600 },
    select: { width: "100%", background: T.panel2, color: T.text, border: `1px solid ${T.line}`, borderRadius: 8, padding: "10px 12px", fontSize: 14 },
    ta: { width: "100%", minHeight: 110, background: T.panel2, color: T.text, border: `1px solid ${T.line}`, borderRadius: 8, padding: 12, fontSize: 14, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" },
    btn: (primary, disabled) => ({
      background: primary ? T.amber : "transparent", color: primary ? "#1A1205" : T.amber,
      border: `1px solid ${T.amber}`, borderRadius: 8, padding: "10px 18px",
      fontWeight: 700, fontSize: 14, cursor: disabled ? "wait" : "pointer", opacity: disabled ? 0.6 : 1,
    }),
    chip: { background: T.amberSoft, border: `1px solid ${T.amber}44`, color: T.text, borderRadius: 10, padding: "10px 12px", cursor: "pointer", textAlign: "left", fontSize: 13, flex: "1 1 240px", fontFamily: "inherit" },
    row: { display: "flex", gap: 12, flexWrap: "wrap" },
    lineCard: (host, active) => ({
      borderLeft: `3px solid ${host ? T.amber : T.teal}`,
      background: active ? "#243140" : T.panel2,
      borderRadius: 8, padding: "10px 14px", marginBottom: 10,
      transition: "background 0.3s",
    }),
  };

  return (
    <div style={S.app}>
      <div style={S.wrap}>
        <div style={S.eyebrow}>● REC&nbsp;&nbsp;REEL STUDIO</div>
        <h1 style={S.h1}>Script &amp; Voice Mapping</h1>
        <div style={S.sub}>Research → Script (45–50s) → ElevenLabs voice preview</div>

        {error && (
          <div style={{ ...S.panel, borderColor: T.red, color: T.red, fontSize: 13 }}>{error}</div>
        )}

        <div style={S.panel}>
          <label style={S.label}>1 · Topic research agent</label>
          <div style={S.row}>
            <select style={{ ...S.select, flex: 1, minWidth: 220 }} value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
            <button style={S.btn(false, loadingTopics)} onClick={fetchTrending} disabled={loadingTopics}>
              {loadingTopics ? "Searching the web…" : "Find trending topics"}
            </button>
          </div>
          {trending.length > 0 && (
            <div style={{ ...S.row, marginTop: 14 }}>
              {trending.map((t, i) => (
                <button key={i} style={S.chip} onClick={() => generateScript(`${t.topic} — ${t.angle}`)}>
                  <div style={{ fontWeight: 700 }}>{t.topic}</div>
                  <div style={{ color: T.dim, marginTop: 4 }}>{t.angle}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div style={S.panel}>
          <label style={S.label}>2 · Script input — or write your own idea</label>
          <textarea
            style={S.ta}
            placeholder="Paste a draft script or describe the reel you want… (leave empty to auto-generate from the category above)"
            value={scriptInput}
            onChange={(e) => setScriptInput(e.target.value)}
          />
          <div style={{ marginTop: 12 }}>
            <button style={S.btn(true, generating)} onClick={() => generateScript(null)} disabled={generating}>
              {generating ? "Writing script…" : "Generate 45–50s reel script"}
            </button>
          </div>
        </div>

        <div style={S.panel}>
          <label style={S.label}>3 · Voice mapping (ElevenLabs — live from your account)</label>
          <div style={S.row}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <label style={{ ...S.label, marginBottom: 6 }}>Host voice</label>
              <select style={S.select} value={hostVoice} onChange={(e) => setHostVoice(e.target.value)}>
                {voices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
              </select>
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <label style={{ ...S.label, marginBottom: 6 }}>Guest voice</label>
              <select style={S.select} value={guestVoice} onChange={(e) => setGuestVoice(e.target.value)}>
                {voices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
              </select>
            </div>
          </div>
        </div>

        {script && (
          <div style={S.panel}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
              <label style={S.label}>4 · Preview — {script.title}</label>
              <div style={{ fontSize: 13, color: inBudget ? T.teal : T.red, fontWeight: 700 }}>
                ⏱ {totalSec}s {inBudget ? "· within budget" : "· outside 45–50s budget"}
              </div>
            </div>

            <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", margin: "6px 0 16px", border: `1px solid ${T.line}` }}>
              {groupedLines.map((l, i) => (
                <div key={i} title={`${l.speaker} · ${Math.round(l.seconds)}s`}
                  style={{ width: `${(l.seconds / Math.max(totalSec, 1)) * 100}%`, background: l.speaker === "HOST" ? T.amber : T.teal }} />
              ))}
            </div>

            {groupedLines.map((l, i) => (
              <div key={i} style={S.lineCard(l.speaker === "HOST", playingIdx === i)}>
                <div style={{ fontSize: 11, color: T.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
                  {playingIdx === i ? "▶ " : ""}{l.speaker} · <em>{l.emotion}</em> · {Math.round(l.seconds)}s
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.5 }}>{l.text}</div>
              </div>
            ))}

            <div style={{ marginTop: 14, display: "flex", gap: 12, flexWrap: "wrap" }}>
              <button style={S.btn(true, synthesizing)} onClick={previewVoices} disabled={synthesizing}>
                {synthesizing ? "Synthesizing voices…" : "▶ Preview with voices"}
              </button>
              {playingIdx >= 0 && (
                <button style={S.btn(false, false)} onClick={stopPlayback}>■ Stop</button>
              )}
              <button style={S.btn(false, generating)} onClick={() => generateScript(null)} disabled={generating}>
                ↻ Regenerate
              </button>
            </div>
          </div>
        )}

        {script && (
          <div style={S.panel}>
            <label style={S.label}>5 · Render &amp; export</label>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <button style={S.btn(true, rendering)} onClick={renderFinal} disabled={rendering}>
                {rendering ? "Rendering audio…" : "⬇ Render final audio + subtitles"}
              </button>
              <button style={S.btn(false, planning)} onClick={planScenes} disabled={planning}>
                {planning ? "Planning scenes…" : "🎬 Generate scene blueprint"}
              </button>
            </div>

            {renderResult && (
              <div style={{ marginTop: 16 }}>
                <audio controls src={renderResult.audio_url} style={{ width: "100%" }} />
                <div style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap", fontSize: 13 }}>
                  <a href={renderResult.audio_url} download style={{ color: T.amber }}>Download MP3</a>
                  <a href={renderResult.srt_url} download style={{ color: T.amber }}>Download SRT subtitles</a>
                  <a href={renderResult.script_url} download style={{ color: T.amber }}>Download Script JSON</a>
                  <span style={{ color: T.dim }}>Final length: {renderResult.total_seconds}s</span>
                </div>

                {renderResult.segments?.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 12, color: T.dim, letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600, marginBottom: 8 }}>
                      Video segments (word-safe cuts · one clip per Higgsfield generation)
                    </div>
                    {renderResult.segments.map((s) => (
                      <div key={s.index} style={{ background: T.panel2, border: `1px solid ${T.line}`, borderRadius: 8, padding: "10px 14px", marginBottom: 8 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
                          <span style={{ fontSize: 12, color: T.teal, fontWeight: 700 }}>
                            SEGMENT {s.index} · {s.start_second}–{s.end_second}s ({s.duration}s)
                          </span>
                          <a href={s.audio_url} download style={{ color: T.amber, fontSize: 12 }}>Download clip audio</a>
                        </div>
                        <audio controls src={s.audio_url} style={{ width: "100%", height: 32 }} />
                        <div style={{ fontSize: 12, color: T.dim, marginTop: 6, lineHeight: 1.5 }}>{s.text}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {blueprint && (
              <div style={{ marginTop: 16 }}>
                {blueprint.scenes.map((s) => (
                  <div key={s.scene_number} style={{ background: T.panel2, border: `1px solid ${T.line}`, borderRadius: 8, padding: "12px 14px", marginBottom: 10 }}>
                    <div style={{ fontSize: 12, color: T.amber, fontWeight: 700, marginBottom: 6 }}>
                      SCENE {s.scene_number} · {s.start_second}–{s.end_second}s · {s.speaker_on_camera} on camera
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.7, color: T.text }}>
                      <span style={{ color: T.dim }}>Action:</span> {s.character_action}<br />
                      <span style={{ color: T.dim }}>Expression:</span> {s.facial_expression}<br />
                      <span style={{ color: T.dim }}>Camera:</span> {s.camera_movement}<br />
                      <span style={{ color: T.dim }}>Background:</span> {s.background_environment}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {blueprint && renderResult && (
          <div style={S.panel}>
            <label style={S.label}>6 · Video generation (Seedance 2.0 · 9:16 · 720p)</label>
            {!videoJob && (
              <button style={S.btn(true, startingJob)} onClick={startVideoJob} disabled={startingJob}>
                {startingJob ? "Starting…" : "🎥 Generate all scene videos"}
              </button>
            )}

            {videoJob && (
              <div>
                <div style={{ fontSize: 13, marginBottom: 10 }}>
                  <span style={{ color: videoJob.status === "failed" ? T.red : videoJob.status === "completed" ? T.teal : T.amber, fontWeight: 700 }}>
                    {videoJob.status.toUpperCase()}
                  </span>
                  <span style={{ color: T.dim }}> — {videoJob.step}</span>
                </div>
                {videoJob.error && <div style={{ color: T.red, fontSize: 13, marginBottom: 10 }}>{videoJob.error}</div>}

                {Object.keys(videoJob.images || {}).length > 0 && (
                  <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
                    {Object.entries(videoJob.images).map(([name, url]) => (
                      <div key={name} style={{ textAlign: "center" }}>
                        <img src={url} alt={name} style={{ width: 90, borderRadius: 8, border: `1px solid ${T.line}` }} />
                        <div style={{ fontSize: 11, color: T.dim, marginTop: 4 }}>{name}</div>
                      </div>
                    ))}
                  </div>
                )}

                {(videoJob.scenes || []).map((sc) => (
                  <div key={sc.scene_number} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 12, color: T.teal, fontWeight: 700, marginBottom: 4 }}>SCENE {sc.scene_number}</div>
                    <video controls src={sc.video_url} style={{ width: 200, borderRadius: 8, border: `1px solid ${T.line}` }} />
                  </div>
                ))}

                {videoJob.merged_url && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 12, color: T.amber, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 8 }}>FINAL MERGED REEL</div>
                    <video controls src={videoJob.merged_url} style={{ width: 260, borderRadius: 10, border: `2px solid ${T.amber}` }} />
                    <div style={{ marginTop: 8 }}>
                      <a href={videoJob.merged_url} download style={{ color: T.amber, fontSize: 13 }}>Download merged reel (MP4)</a>
                    </div>
                  </div>
                )}

                {videoJob.status === "failed" && (
                  <>
                    <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                      <button style={S.btn(false, false)} disabled={startingJob}
                        onClick={() => startVideoJob({ retry: true })}>
                        ↻ Retry (resume — keeps finished scenes)
                      </button>
                      <button style={S.btn(false, false)} disabled={startingJob}
                        onClick={() => startVideoJob({ retry: true, forceRegen: true })}>
                        ⟳ Regenerate all scenes
                      </button>
                    </div>
                    <div style={{ fontSize: 12, color: "#888", marginTop: 6 }}>
                      Retry keeps already-generated scenes (same script, 0 extra credits for those) and only continues the missing ones.
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}