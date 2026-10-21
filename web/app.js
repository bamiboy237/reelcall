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
