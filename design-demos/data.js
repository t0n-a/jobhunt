// Sample inbox data for the design demos. Fake jobs, same shape the inbox renders
// (title, company, fit, track, seniority, location, rationale) plus the pipeline status
// and next action the redesign has to show.
window.STAGES = ["found", "ranked", "drafted", "waiting", "sent", "blocked"];
window.STAGE_LABEL = {
  found: "Found", ranked: "Ranked", drafted: "Drafted",
  waiting: "Needs your yes", sent: "Sent", blocked: "Blocked",
};
window.JOBS = [
  { id: 41, title: "Senior Backend Engineer", company: "Lattice Freight", fit: 91, track: "Backend / Python", seniority: "match", where: "Remote (EU)", remote: true,
    status: "waiting", next: "Approve cover letter", age: "2h",
    rationale: "Python + Postgres + event queues match your last two roles; they want someone who has run on-call.",
    flag: { text: "“Reduced p99 latency by 60%”", why: "no evidence atom backs this number" } },
  { id: 38, title: "Platform Engineer", company: "Orbitwise", fit: 87, track: "Platform / Infra", seniority: "match", where: "Lisbon (hybrid)", remote: false,
    status: "drafted", next: "Review tailored CV", age: "5h",
    rationale: "Terraform and Kubernetes show up in four of your atoms; the posting leans heavy on cost work you have done." },
  { id: 44, title: "Staff Software Engineer, Data", company: "Kelpie Health", fit: 84, track: "Data engineering", seniority: "stretch", where: "Remote (Global)", remote: true,
    status: "ranked", next: "Draft application", age: "1d",
    rationale: "Strong pipeline overlap. Staff level is a stretch: they ask for org-wide technical direction." },
  { id: 29, title: "Backend Developer (Go)", company: "Pinecrest Labs", fit: 78, track: "Backend / Go", seniority: "match", where: "Porto", remote: false,
    status: "sent", next: "Follow up on Oct 21", age: "4d",
    rationale: "Go is a gap you are closing; the rest is a close match." },
  { id: 47, title: "Software Engineer II", company: "Cardinal Maps", fit: 74, track: "Backend / Python", seniority: "under", where: "Remote (EU)", remote: true,
    status: "blocked", next: "Answer the visa question", age: "1d",
    rationale: "Good stack match, one level below you. The form asks about work authorisation in Germany.",
    flag: { text: "Form field “Years with Rust”", why: "no answer on file, left blank" } },
  { id: 50, title: "Infrastructure Engineer", company: "Tallow & Finch", fit: 69, track: "Platform / Infra", seniority: "match", where: "Remote (Portugal)", remote: true,
    status: "ranked", next: "Draft application", age: "3h",
    rationale: "Solid infra overlap; small team, lots of on-prem hardware you have not touched." },
  { id: 52, title: "Python Engineer, ML Platform", company: "Quillion", fit: 66, track: "ML platform", seniority: "match", where: "Madrid", remote: false,
    status: "found", next: "Score it", age: "20m",
    rationale: "" },
  { id: 33, title: "Site Reliability Engineer", company: "Brightwater Energy", fit: 61, track: "Platform / Infra", seniority: "stretch", where: "Remote (EU)", remote: true,
    status: "sent", next: "Wait (sent 6d ago)", age: "6d",
    rationale: "SRE title, but the work is mostly backend. Your incident write-ups fit." },
  { id: 55, title: "Full-stack Engineer", company: "Moth Studio", fit: 48, track: "Full-stack", seniority: "under", where: "Lisbon", remote: false,
    status: "ranked", next: "Skip or draft", age: "2d",
    rationale: "Heavy React front-end work; weak fit with your evidence." },
];
window.TRACKS = [["Backend / Python", 2], ["Platform / Infra", 3], ["Data engineering", 1], ["Backend / Go", 1], ["ML platform", 1], ["Full-stack", 1]];
