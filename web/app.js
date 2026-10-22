// Install command tabs (uv / git): swap which command line is shown.
const tabs = document.querySelectorAll(".tab");
const commandLines = document.querySelectorAll("[data-command]");

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    if (tab.classList.contains("is-active")) return;

    for (const other of tabs) {
      const isTarget = other === tab;
      other.classList.toggle("is-active", isTarget);
      other.setAttribute("aria-selected", String(isTarget));
    }
    for (const line of commandLines) {
      line.hidden = line.dataset.command !== target;
    }
  });
}

// Copy install command to the clipboard, with a short text swap for feedback.
const copyButton = document.querySelector(".copy");
const copyLabel = copyButton?.querySelector(".copy__label");
let copyResetTimer;

copyButton?.addEventListener("click", async () => {
  const activeLine = document.querySelector("[data-command]:not([hidden])");
  const command = activeLine?.textContent ?? "";

  try {
    await navigator.clipboard.writeText(command);
  } catch {
    return;
  }

  copyButton.classList.add("is-copied");
  if (copyLabel) copyLabel.textContent = "copied";

  clearTimeout(copyResetTimer);
  copyResetTimer = setTimeout(() => {
    copyButton.classList.remove("is-copied");
    if (copyLabel) copyLabel.textContent = "copy";
  }, 1500);
});

// Product demo: one terminal pane auto-plays add -> index -> search -> play.
// Marketing/explanatory motion, so it loops while on screen. It pauses when
// off-screen or the tab is hidden, and renders a static final scene when
// the user prefers reduced motion.
const demo = document.querySelector("[data-demo]");

if (demo) {
  const scenes = demo.querySelectorAll("[data-scene]");
  const keyEls = demo.querySelectorAll("[data-demo-keys] .key");
  const pathEl = demo.querySelector("[data-type-path]");
  const queryEl = demo.querySelector("[data-type-query]");
  const progress = demo.querySelector("[data-progress]");
  const results = demo.querySelector("[data-results]");
  const resultButton = demo.querySelector("[data-result]");
  const player = demo.querySelector("[data-player]");
  const playhead = demo.querySelector("[data-playhead]");
  const playheadTime = playhead?.querySelector(".timeline__time");

  const PATH_TEXT = "~/Videos/witch-hat.mp4";
  const QUERY_TEXT = "the girl in a black witch hat";
  const MATCH_TIME = 24;
  const TIMELINE_END = 32;
  const SEARCH_DELAY = 600;
  const QUERY_DELAY = 70;
  const RESULT_DELAY = 600;
  const SCENE_KEYS = {
    add: [["⏎", "add"], ["esc", "cancel"]],
    index: [["…", "indexing"], ["esc", "cancel"]],
    search: [["⏎", "search"], ["esc", "clear"]],
    play: [["space", "pause"], ["esc", "back"]],
  };

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let timers = [];
  let cycleId = 0;
  let inView = false;
  let searchScan;

  function placePlayhead(seconds) {
    searchScan?.cancel();
    searchScan = null;
    if (playhead) playhead.style.transform = `translateX(${seconds / TIMELINE_END * 100}%)`;
    if (playheadTime) playheadTime.textContent = `0:${String(seconds).padStart(2, "0")}`;
    demo.classList.remove("is-searching");
  }

  function later(fn, ms) {
    timers.push(setTimeout(fn, ms));
  }

  function stopCycle() {
    cycleId++;
    for (const timer of timers) clearTimeout(timer);
    timers = [];
    searchScan?.cancel();
    searchScan = null;
    demo.classList.remove("is-searching");
  }

  function setScene(name) {
    demo.dataset.currentScene = name;
    for (const scene of scenes) {
      const active = scene.dataset.scene === name;
      scene.classList.toggle("is-active", active);
      scene.setAttribute("aria-hidden", String(!active));
    }
    const keys = SCENE_KEYS[name];
    if (keys) {
      keyEls.forEach((keyEl, i) => {
        const [kbd, text] = keys[i] ?? ["", ""];
        const kbdEl = keyEl.querySelector("kbd");
        const textEl = keyEl.querySelector("span");
        if (kbdEl) kbdEl.textContent = kbd;
        if (textEl) textEl.textContent = text;
      });
    }
  }

  function typeText(el, text, delay, done) {
    let i = 0;
    const step = () => {
      el.textContent = text.slice(0, ++i);
      if (i < text.length) later(step, delay);
      else if (done) done();
    };
    later(step, delay);
  }

  function showIndex() {
    setScene("index");
    later(() => progress?.classList.add("is-running"), 60);
    later(showSearch, 4000);
  }

  function showSearch() {
    setScene("search");
    demo.classList.add("is-searching");
    if (playhead) {
      playhead.style.transform = `translateX(${MATCH_TIME / TIMELINE_END * 100}%)`;
      searchScan = playhead.animate([
        { transform: "translateX(0%)" },
        { transform: playhead.style.transform },
      ], { duration: SEARCH_DELAY + QUERY_TEXT.length * QUERY_DELAY + RESULT_DELAY, easing: "linear" });
    }
    later(() => {
      typeText(queryEl, QUERY_TEXT, QUERY_DELAY, () => {
        later(() => {
          results?.classList.add("is-visible");
          placePlayhead(MATCH_TIME);
        }, RESULT_DELAY);
        later(showPlay, 3400);
      });
    }, SEARCH_DELAY);
  }

  function showPlay() {
    setScene("play");
    placePlayhead(MATCH_TIME);
    if (player) {
      try { player.currentTime = 0; } catch { /* metadata not loaded yet */ }
      player.play()?.catch(() => {});
    }
    later(() => { if (inView) startCycle(); }, 8500);
  }

  function startCycle() {
    stopCycle();
    const gen = cycleId;
    const guard = (fn) => () => { if (gen === cycleId) fn(); };
    setScene("add");
    demo.classList.remove("is-keyboard");
    placePlayhead(0);
    if (pathEl) pathEl.textContent = "";
    if (queryEl) queryEl.textContent = "";
    results?.classList.remove("is-visible");
    progress?.classList.remove("is-running");
    player?.pause();
    later(guard(() => typeText(pathEl, PATH_TEXT, 75, guard(() => later(guard(showIndex), 800)))), 650);
  }

  resultButton?.addEventListener("click", (event) => {
    stopCycle();
    demo.classList.toggle("is-keyboard", event.detail === 0);
    showPlay();
  });

  if (reduceMotion) {
    // Static final scene: query and result visible, clip paused with controls.
    if (pathEl) pathEl.textContent = PATH_TEXT;
    if (queryEl) queryEl.textContent = QUERY_TEXT;
    results?.classList.add("is-visible");
    setScene("play");
    placePlayhead(MATCH_TIME);
    player?.pause();
  } else if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.target !== demo) continue;
        inView = entry.isIntersecting;
        if (inView) startCycle();
        else { stopCycle(); player?.pause(); }
      }
    }, { threshold: 0.35 });
    observer.observe(demo);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { stopCycle(); player?.pause(); }
      else if (inView) startCycle();
    });
  } else {
    inView = true;
    startCycle();
  }
}
