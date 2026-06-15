// const BASE = "/api";

// async function post(path, body) {
//   const res = await fetch(`${BASE}${path}`, {
//     method: "POST",
//     headers: { "Content-Type": "application/json" },
//     body: JSON.stringify(body),
//   });
//   if (!res.ok) {
//     const err = await res.json().catch(() => ({}));
//     throw new Error(err.detail?.toString?.() || `Request failed (${res.status})`);
//   }
//   return res.json();
// }

// export const api = {
//   research: (category) => post("/research", { category }),
//   script: ({ category, seed_topic, user_draft }) =>
//     post("/script", { category, seed_topic, user_draft }),
//   voices: async () => {
//     const res = await fetch(`${BASE}/voices`);
//     if (!res.ok) throw new Error("Couldn't load ElevenLabs voices");
//     return res.json();
//   },
//   ttsPreview: (lines, host_voice_id, guest_voice_id) =>
//     post("/tts-preview", { lines, host_voice_id, guest_voice_id }),
//   render: (title, lines, host_voice_id, guest_voice_id) =>
//     post("/render", { title, lines, host_voice_id, guest_voice_id }),
//   scenePlan: (title, lines, segments) => post("/scene-plan", { title, lines, segments }),
//   generateVideos: (render_id, blueprint) => post("/generate-videos", { render_id, blueprint }),
//   retryVideos: (render_id, blueprint, force_regen_scenes = false) => post("/retry-videos", { render_id, blueprint, force_regen_scenes }),
//   characters: async () => (await fetch(`${BASE}/characters`)).json(),
//   uploadCharacter: async (role, file) => {
//     const fd = new FormData();
//     fd.append("file", file);
//     const res = await fetch(`${BASE}/characters/${role}`, { method: "POST", body: fd });
//     if (!res.ok) throw new Error("Upload failed");
//     return res.json();
//   },
//   videoJob: async (jobId) => {
//     const res = await fetch(`${BASE}/video-jobs/${jobId}`);
//     if (!res.ok) throw new Error("Job not found");
//     return res.json();
//   },
// };

const BASE = "/api";

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.toString?.() || `Request failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  research: (category) => post("/research", { category }),
  script: ({ category, seed_topic, user_draft }) =>
    post("/script", { category, seed_topic, user_draft }),
  voices: async () => {
    const res = await fetch(`${BASE}/voices`);
    if (!res.ok) throw new Error("Couldn't load ElevenLabs voices");
    return res.json();
  },
  ttsPreview: (lines, host_voice_id, guest_voice_id) =>
    post("/tts-preview", { lines, host_voice_id, guest_voice_id }),
  render: (title, lines, host_voice_id, guest_voice_id) =>
    post("/render", { title, lines, host_voice_id, guest_voice_id }),
  scenePlan: (title, lines, segments) => post("/scene-plan", { title, lines, segments }),
  generateVideos: (render_id, blueprint) => post("/generate-videos", { render_id, blueprint }),
  retryVideos: (render_id, blueprint, force_regen_scenes = false) => post("/retry-videos", { render_id, blueprint, force_regen_scenes }),
  characters: async () => (await fetch(`${BASE}/characters`)).json(),
  uploadCharacter: async (role, file) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE}/characters/${role}`, { method: "POST", body: fd });
    if (!res.ok) throw new Error("Upload failed");
    return res.json();
  },
  videoJob: async (jobId) => {
    const res = await fetch(`${BASE}/video-jobs/${jobId}`);
    if (!res.ok) throw new Error("Job not found");
    return res.json();
  },
};