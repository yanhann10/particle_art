/** Persist one idempotent raw curation event to GitHub. */
const PATH = "feedback_log.jsonl";
const BRANCH = "main";
export const config = { runtime: "edge" };

export default async function handler(req) {
  const headers = { "Access-Control-Allow-Origin":"*", "Content-Type":"application/json" };
  if (req.method === "OPTIONS") return new Response(null, { status:204, headers });
  if (req.method !== "POST") return new Response(JSON.stringify({error:"POST only"}), {status:405, headers});
  const event = await req.json().catch(() => null);
  if (!event?.id || !event?.piece_id || !["keep","evolve","remove","undo-remove"].includes(event.action)) {
    return new Response(JSON.stringify({error:"invalid feedback event"}), {status:400, headers});
  }
  const token=process.env.GITHUB_TOKEN, repo=process.env.GITHUB_REPO;
  if (!token || !repo) return new Response(JSON.stringify({ok:false,reason:"no github config"}), {status:503,headers});
  const url=`https://api.github.com/repos/${repo}/contents/${PATH}`;
  const gh={Authorization:`Bearer ${token}`,Accept:"application/vnd.github+json","Content-Type":"application/json","X-GitHub-Api-Version":"2022-11-28"};
  for (let attempt=0; attempt<3; attempt++) {
    const current=await fetch(`${url}?ref=${BRANCH}&t=${Date.now()}`,{headers:gh});
    let sha=null,text="";
    if (current.ok) { const file=await current.json();sha=file.sha;text=decodeURIComponent(escape(atob(file.content.replace(/\n/g,"")))); }
    else if (current.status!==404) return new Response(JSON.stringify({ok:false,error:`GitHub GET ${current.status}`}),{status:502,headers});
    if (text.split("\n").some(line=>line.includes(`\"id\":\"${event.id}\"`))) return new Response(JSON.stringify({ok:true,id:event.id,duplicate:true}),{headers});
    const recorded={...event,recorded_at:new Date().toISOString()};
    const content=btoa(unescape(encodeURIComponent(text+JSON.stringify(recorded)+"\n")));
    const put=await fetch(url,{method:"PUT",headers:gh,body:JSON.stringify({message:`feedback: ${event.action} ${event.piece_id}`,content,branch:BRANCH,...(sha?{sha}:{})})});
    if (put.ok) return new Response(JSON.stringify({ok:true,id:event.id}),{headers});
    if (put.status!==409) return new Response(JSON.stringify({ok:false,error:`GitHub PUT ${put.status}`}),{status:502,headers});
  }
  return new Response(JSON.stringify({ok:false,error:"concurrent update; retry"}),{status:409,headers});
}
