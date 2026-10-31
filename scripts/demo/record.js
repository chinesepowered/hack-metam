// Record the demo: drive mission control through a real session, scene by scene, timed to the narration.
//   node scripts/demo/record.js runs/demo/timeline.json http://127.0.0.1:8000 cycle-1
// Needs Playwright (preinstalled in this environment; elsewhere `pnpm add -D playwright`).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [timelinePath, base = "http://127.0.0.1:8000", session = "cycle-1"] = process.argv.slice(2);
const timeline = JSON.parse(fs.readFileSync(timelinePath, "utf8"));
const outDir = path.join(path.dirname(timelinePath), "video");
const W = 1600, H = 900, PAD = 0.6; // seconds of breathing room after each line

const CAPTION_CSS = `#cap{position:fixed;left:50%;bottom:34px;transform:translateX(-50%);max-width:1180px;z-index:99;
  background:rgba(4,10,22,.86);color:#fff;font:600 25px/1.35 ui-sans-serif,system-ui,sans-serif;padding:12px 22px;
  border-radius:14px;border:1px solid rgba(255,255,255,.12);text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.45)}`;

function sentences(text) {
  return text.match(/[^.!?]+[.!?]+/g)?.map((s) => s.trim()) || [text];
}

async function captions(page, text, seconds) {
  await page.addStyleTag({ content: CAPTION_CSS }).catch(() => {});
  const parts = sentences(text);
  const total = parts.reduce((n, s) => n + s.length, 0);
  for (const s of parts) {
    await page.evaluate((t) => {
      let el = document.getElementById("cap");
      if (!el) { el = document.createElement("div"); el.id = "cap"; document.body.appendChild(el); }
      el.textContent = t;
    }, s);
    await page.waitForTimeout((s.length / total) * seconds * 1000);
  }
}

// Named cut points: r1 = round 1 started, r1_review = first verdict of round 1,
// r1_decide = Director starts ruling on round 1, …, bulletin, end.
function boundaries(events) {
  const b = { end: events.length };
  let round = 0;
  events.forEach((e, i) => {
    if (e.kind === "round") { round = e.round; b[`r${round}`] = i + 1; }
    if (round && e.kind === "review" && b[`r${round}_review`] === undefined) b[`r${round}_review`] = i;
    if (round && e.actor === "director" && b[`r${round}_review`] !== undefined && b[`r${round}_decide`] === undefined) b[`r${round}_decide`] = i;
    if (e.kind === "bulletin") b.bulletin = i + 1;
  });
  return b;
}

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: outDir, size: { width: W, height: H } } });
  const t0 = Date.now();
  const page = await context.newPage();
  const marks = [];
  let inReplay = false, bounds = null;

  for (const scene of timeline) {
    const seconds = scene.seconds + PAD;
    if (scene.visual.startsWith("replay:")) {
      if (!inReplay) {
        await page.goto(`${base}/?session=${session}&replay=manual`, { waitUntil: "networkidle" });
        await page.waitForFunction(() => window.orbital && window.orbital.events.length > 0);
        bounds = boundaries(await page.evaluate(() => window.orbital.events));
        inReplay = true;
      }
      const target = bounds[scene.visual.split(":")[1]];
      if (target === undefined) throw new Error(`no cut point ${scene.visual}; have ${Object.keys(bounds)}`);
      marks.push({ id: scene.id, start: (Date.now() - t0) / 1000 });
      const play = page.evaluate(([end, ms]) => window.orbital.playTo(end, ms), [target, Math.max(500, (seconds - 1.2) * 1000)]);
      await captions(page, scene.text, seconds);
      await play;
      if (scene.visual === "replay:end") {
        await page.evaluate(() => window.orbital.showBulletin());
      }
    } else {
      inReplay = false;
      await page.goto(base + scene.visual, { waitUntil: "networkidle" });
      await page.waitForTimeout(400);
      marks.push({ id: scene.id, start: (Date.now() - t0) / 1000 });
      await captions(page, scene.text, seconds);
    }
  }
  await page.waitForTimeout(1500);
  const video = page.video();
  await context.close();
  await browser.close();
  const raw = await video.path();
  fs.writeFileSync(path.join(outDir, "marks.json"), JSON.stringify({ video: raw, marks }, null, 1));
  console.log("recorded", raw);
})();
