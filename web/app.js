// Copy the install command. The label blurs between "copy" and "copied".
for (const button of document.querySelectorAll("[data-copy]")) {
  const label = button.querySelector(".copy-label");
  let resetTimer;

  const swap = (text) => {
    label.classList.add("swapping");
    setTimeout(() => {
      label.textContent = text;
      label.classList.remove("swapping");
    }, 120);
  };

  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      swap("copied");
    } catch {
      swap(/Mac|iPhone|iPad/.test(navigator.platform) ? "press ⌘C" : "press Ctrl+C");
    }
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => swap("copy"), 1600);
  });
}

// The demo replays one session of Reelcall's terminal UI on a loop:
//   add a video, index it in overlapping windows, search, play the best match.
// The rail under the screen measures the same clip, so each step marks it.
// It runs only while on screen. With reduced motion it holds the search result,
// which is the page's static markup.
const demo = document.querySelector("[data-demo]");

if (demo && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const TRACK_SECONDS = 32;
  const BEST_START = 24;
  const HIT_STARTS = [20, 4];
  const PATH_TEXT = "~/Videos/witch-hat.mp4";
  const QUERY_TEXT = "the girl in a black witch hat";
  const KEYS = {
    add: [["⏎", "add"], ["esc", "cancel"]],
    index: [["a", "add"], ["/", "search"], ["⏎", "open"], ["!", "activity"]],
    search: [["⏎", "play"], ["f", "filter"], ["/", "edit"], ["esc", "back"]],
    play: [["space", "pause"], ["← →", "seek"], ["m", "mute"], ["esc", "back"]],
  };

  const windows = [...demo.querySelectorAll("[data-window]")];
  const keybar = demo.querySelector("[data-keys]");
  const pathEl = demo.querySelector("[data-type-path]");
  const queryEl = demo.querySelector("[data-type-query]");
  const stateEl = demo.querySelector("[data-state]");
  const summaryEl = demo.querySelector("[data-summary]");
  const progressEl = demo.querySelector("[data-progress]");
  const progressText = demo.querySelector("[data-progress-text]");
  const scan = demo.querySelector("[data-scan]");
  const playhead = demo.querySelector("[data-playhead]");
  const playheadTime = demo.querySelector("[data-playhead-time]");
  const resultButton = demo.querySelector("[data-result]");
  const player = demo.querySelector("[data-player]");
  const playerTime = demo.querySelector("[data-player-time]");

  let timers = [];
  let scanAnimation;
  let inView = false;

  const later = (fn, ms) => timers.push(setTimeout(fn, ms));
  const timecode = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  const windowAt = (start) => windows.find((win) => Number(win.style.getPropertyValue("--start")) === start);

  function placePlayhead(seconds) {
    playhead.style.transform = `translateX(${(seconds / TRACK_SECONDS) * 100}%)`;
    playheadTime.textContent = timecode(seconds);
  }

  function setScene(name) {
    demo.dataset.scene = name;
    keybar.replaceChildren(
      ...KEYS[name].map(([key, action]) => {
        const item = document.createElement("span");
        const kbd = document.createElement("kbd");
        kbd.textContent = key;
        item.append(kbd, action);
        return item;
      }),
    );
  }

  function typeText(el, text, delay, done) {
    let typed = 0;
    const step = () => {
      el.textContent = text.slice(0, ++typed);
      later(typed < text.length ? step : done, typed < text.length ? delay : 0);
    };
    later(step, delay);
  }

  function stop() {
    for (const timer of timers) clearTimeout(timer);
    timers = [];
    scanAnimation?.cancel();
    demo.classList.remove("is-scanning");
    player.pause();
  }

  function reset() {
    stop();
    demo.classList.remove("has-results");
    for (const win of windows) win.classList.remove("is-indexed", "is-hit", "is-best");
    pathEl.textContent = "";
    queryEl.textContent = "";
    stateEl.textContent = "◐";
    summaryEl.textContent = "◐ 1";
    progressEl.style.setProperty("--progress", 0);
    progressText.textContent = `window 0 of ${windows.length}`;
  }

  function showAdd() {
    reset();
    setScene("add");
    typeText(pathEl, PATH_TEXT, 70, () => later(showIndex, 900));
  }

  // One window is embedded at a time, left to right, as the worker does it.
  function showIndex() {
    setScene("index");
    windows.forEach((win, i) => {
      later(() => {
        win.classList.add("is-indexed");
        progressEl.style.setProperty("--progress", (i + 1) / windows.length);
        progressText.textContent = `window ${i + 1} of ${windows.length}`;
      }, 500 + i * 520);
    });
    later(() => {
      stateEl.textContent = "●";
      summaryEl.textContent = "";
    }, 500 + windows.length * 520);
    later(showSearch, 1400 + windows.length * 520);
  }

  // The query is compared with every window; the scan line sweeps the clip.
  function showSearch() {
    setScene("search");
    later(() => {
      typeText(queryEl, QUERY_TEXT, 65, () => {
        demo.classList.add("is-scanning");
        scanAnimation = scan.animate([{ transform: "translateX(0%)" }, { transform: "translateX(100%)" }], {
          duration: 1100,
          easing: "cubic-bezier(0.65, 0, 0.35, 1)",
        });
        scanAnimation.onfinish = () => {
          demo.classList.remove("is-scanning");
          for (const start of HIT_STARTS) windowAt(start)?.classList.add("is-hit");
          windowAt(BEST_START)?.classList.add("is-best");
          placePlayhead(BEST_START);
          demo.classList.add("has-results");
          later(showPlay, 3600);
        };
      });
    }, 500);
  }

  function showPlay() {
    stop();
    setScene("play");
    placePlayhead(BEST_START);
    try {
      player.currentTime = 0;
    } catch {
      // Metadata has not loaded yet; playback starts from the beginning anyway.
    }
    player.play()?.catch(() => {});
    later(() => {
      if (inView) showAdd();
    }, 9000);
  }

  // The playhead follows the clip, offset to where the match starts in the source.
  player.addEventListener("timeupdate", () => {
    if (demo.dataset.scene !== "play") return;
    const seconds = BEST_START + player.currentTime;
    placePlayhead(seconds);
    playerTime.textContent = `${timecode(seconds)} / ${timecode(TRACK_SECONDS)}`;
  });

  resultButton.addEventListener("click", showPlay);

  const update = () => {
    if (inView && !document.hidden) showAdd();
    else stop();
  };

  new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting === inView) return;
      inView = entry.isIntersecting;
      update();
    },
    { threshold: 0.35 },
  ).observe(demo);
  document.addEventListener("visibilitychange", update);
}
