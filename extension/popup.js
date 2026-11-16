const API = "http://localhost:5580/api/autofill-data";

// This function is serialized and executed INSIDE the page (all frames).
// It must be fully self-contained.
function fillPage(data) {
  const { profile, answers } = data;
  const filled = [], unknown = [];

  const norm = s => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

  function labelFor(el) {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && l.textContent.trim()) return l.textContent;
    }
    const wrap = el.closest("label");
    if (wrap && wrap.textContent.trim()) return wrap.textContent;
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    // aria-labelledby may list SEVERAL ids ("q-label q-hint") — join them all.
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const txt = lb.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean).map(n => n.textContent).join(" ").trim();
      if (txt) return txt;
    }
    if (el.getAttribute("aria-describedby")) {
      const n = document.getElementById(el.getAttribute("aria-describedby"));
      if (n && n.textContent.trim()) return n.textContent;
    }
    // Last resort, and the one that saves us on unknown ATSes: climb to the
    // field's container and use its text minus the control's own text.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
      const own = (node.innerText || "").replace(el.value || "", "").trim();
      if (own.length > 8 && own.length < 400) return own;
    }
    return el.placeholder || el.name || "";
  }

  const STOP = new Set(["the", "a", "an", "of", "do", "you", "have", "your", "with",
    "and", "or", "in", "on", "for", "to", "is", "are", "how", "many", "what", "please",
    "specifically", "not", "just", "years", "year", "experience", "hands"]);

  function tokens(s) {
    return norm(s).replace(/[^a-z0-9+#. ]/g, " ").split(/\s+/).filter(Boolean);
  }

  // Whole-word containment. Plain .includes() is a trap here: "eks" matches
  // "we<eks>" and "1" matches "<1>5 days", which silently fills wrong answers.
  function wordMatch(haystack, needle) {
    if (!needle) return false;
    const esc = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?<![a-z0-9])${esc}(?![a-z0-9])`, "i").test(haystack);
  }

  function answerFor(label, el) {
    const q = norm(label);
    const nm = norm(el.name + " " + el.id + " " + (el.autocomplete || ""));
    // profile-field heuristics first (ATS forms use standard names)
    // NOTE: every pattern is word-bounded on purpose. Without \b, /tel/ matches
    // "immediaTELy" and hijacks a notice-period question with a phone number.
    const direct = [
      [/\b(first|given)[\s-]?name\b/, profile.first_name],
      [/\b(last|family)[\s-]?name\b|\bsurname\b/, profile.last_name],
      [/\bfull[\s-]?name\b|^name$|\byour name\b/, profile.name],
      [/\be-?mail\b/, profile.email],
      [/\b(phone|mobile|telephone|contact number)\b/, profile.phone],
      [/\bgithub\b/, profile.github],
      [/\blinkedin\b/, profile.linkedin],
      [/\b(location|city|current address|based in)\b/, profile.location],
      [/\bcover[\s-]?letter\b|\bwhy\b.*\b(join|interested|fit)\b|\btell us about\b/,
       profile.summary],
    ];
    for (const [re, val] of direct) {
      if (val && (re.test(q) || re.test(nm))) return { value: val };
    }
    // Fuzzy: recruiters never phrase questions like our patterns. Require every
    // meaningful word of a pattern to appear somewhere in the question, and
    // prefer the most specific pattern that qualifies.
    const qTokens = new Set(tokens(label));
    let best = null, bestSpecificity = 0;
    for (const a of answers) {
      for (const pat of a.match) {
        if (wordMatch(q, norm(pat))) return a;          // exact phrase wins
        const content = tokens(pat).filter(t => !STOP.has(t));
        if (!content.length) continue;
        if (!content.every(t => qTokens.has(t))) continue;
        // a "years of X" answer must only land on a duration question
        if (/years?/.test(pat) && !/(year|how long|duration|months)/.test(q)) continue;
        if (content.length > bestSpecificity) { best = a; bestSpecificity = content.length; }
      }
    }
    return best;
  }

  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const inputs = document.querySelectorAll(
    "input[type=text], input[type=email], input[type=tel], input[type=url], " +
    "input[type=number], input:not([type]), textarea");
  for (const el of inputs) {
    if (el.value && el.value.trim()) continue;
    if (!visible(el)) continue;
    if (el.type === "search" || /search/i.test(el.name + el.id)) continue;
    const label = labelFor(el);
    const ans = answerFor(label, el);
    if (ans) {
      setNative(el, String(ans.value));
      el.style.outline = "2px solid #16a34a";
      filled.push(norm(label).slice(0, 60));
    } else {
      // never skip silently — an unlabeled field is a diagnosis, not a no-op
      unknown.push((norm(label) || `(unlabeled ${el.tagName.toLowerCase()} ` +
        `name=${el.name || "?"} id=${el.id || "?"})`).slice(0, 110));
    }
  }

  for (const sel of document.querySelectorAll("select")) {
    if (!visible(sel) || sel.selectedIndex > 0) continue;
    const label = labelFor(sel);
    const ans = answerFor(label, sel);
    if (!ans) { unknown.push((norm(label) || "(unlabeled select)").slice(0, 110)); continue; }
    const target = typeof ans.value === "boolean" ? (ans.value ? "yes" : "no")
      : String(ans.value).toLowerCase();
    let picked = false;
    for (const opt of sel.options) {
      if (opt.value && wordMatch(norm(opt.textContent), target)) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sel.style.outline = "2px solid #16a34a";
        filled.push(norm(label).slice(0, 60));
        picked = true;
        break;
      }
    }
    // "Immediately available" won't literally appear; try the option that
    // shares the most words with our answer.
    if (!picked) {
      const want = new Set(tokens(String(ans.value)));
      let bestOpt = null, bestHits = 0;
      for (const opt of sel.options) {
        if (!opt.value) continue;
        const hits = tokens(opt.textContent).filter(t => want.has(t)).length;
        if (hits > bestHits) { bestHits = hits; bestOpt = opt; }
      }
      if (bestOpt && bestHits > 0) {
        sel.value = bestOpt.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sel.style.outline = "2px solid #16a34a";
        filled.push(norm(label).slice(0, 60));
      } else {
        unknown.push(`${norm(label).slice(0, 70)} → options: ` +
          Array.from(sel.options).slice(1, 5).map(o => o.textContent.trim()).join(" | "));
      }
    }
  }

  // radios: match group label via fieldset legend
  for (const fs of document.querySelectorAll("fieldset")) {
    const legend = fs.querySelector("legend");
    if (!legend) continue;
    if (fs.querySelector("input[type=radio]:checked")) continue;
    const ans = answerFor(legend.textContent, fs);
    if (!ans) continue;
    const target = typeof ans.value === "boolean" ? (ans.value ? "yes" : "no")
      : String(ans.value).toLowerCase();
    for (const lab of fs.querySelectorAll("label")) {
      if (wordMatch(norm(lab.textContent), target)) {
        lab.click();
        filled.push(norm(legend.textContent).slice(0, 60));
        break;
      }
    }
  }

  let files = 0;
  for (const f of document.querySelectorAll("input[type=file]")) {
    f.style.outline = "3px dashed #b45309";
    f.scrollIntoView({ block: "center", behavior: "smooth" });
    files++;
  }

  return { filled, unknown: [...new Set(unknown)], files };
}

document.getElementById("fill").addEventListener("click", async () => {
  const out = document.getElementById("out");
  const btn = document.getElementById("fill");
  btn.disabled = true;
  out.textContent = "Loading profile…";
  try {
    const data = await (await fetch(API)).json();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: fillPage,
      args: [data],
    });
    let filled = [], unknown = [], files = 0;
    for (const r of results) {
      if (!r.result) continue;
      filled = filled.concat(r.result.filled);
      unknown = unknown.concat(r.result.unknown);
      files += r.result.files;
    }
    out.innerHTML =
      `<div class="ok">✓ Filled ${filled.length} field(s)</div>` +
      (files ? `<div class="warn">📎 ${files} file upload(s) highlighted — attach:<br><code>${data.resume_path}</code></div>` : "") +
      (unknown.length
        ? `<div class="warn">❓ ${unknown.length} unanswered — add to answers.yaml:</div><ul>` +
          unknown.slice(0, 12).map(u => `<li>${u}</li>`).join("") + "</ul>"
        : `<div class="ok">No unknown questions 🎉</div>`) +
      `<p><b>Review everything, then click submit yourself.</b></p>`;
  } catch (e) {
    out.innerHTML = `<div class="err">${String(e).includes("Failed to fetch")
      ? "Local server not running. Start it: python run.py web"
      : "Error: " + e}</div>`;
  } finally {
    btn.disabled = false;
  }
});
