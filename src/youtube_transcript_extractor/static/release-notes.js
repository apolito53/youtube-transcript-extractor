(() => {
  // User-facing release history, newest first. Add meaningful shipped changes
  // in plain language alongside each production release. Use dates only when known.
  const releaseNotes = [
    {
      label: "Release notes",
      title: "A place for updates",
      changes: [
        "Open What's new at the top of the page to see changes to the transcript tool.",
      ],
    },
    {
      label: "Earlier highlights",
      title: "From video to useful text",
      changes: [
        "Turn available YouTube captions into clean Markdown, then copy or download the result.",
        "Choose a caption language and add timestamp links back to the video when you want them.",
        "Use optional AI formatting when available, or keep the clean transcript on its own.",
        "Reuse cached analyses for repeat requests, or refresh captions to save a new version.",
      ],
    },
  ];

  const dialog = document.getElementById("release-notes-dialog");
  const list = document.getElementById("release-notes-list");

  for (const release of releaseNotes) {
    const entry = document.createElement("article");
    entry.className = "release-entry";
    const label = document.createElement("div");
    label.className = "release-entry-date";
    label.textContent = release.label;
    const title = document.createElement("h3");
    title.textContent = release.title;
    const changes = document.createElement("ul");
    for (const change of release.changes) {
      const item = document.createElement("li");
      item.textContent = change;
      changes.appendChild(item);
    }
    entry.append(label, title, changes);
    list.appendChild(entry);
  }

  document.getElementById("release-notes-button").addEventListener("click", () => dialog.showModal());
  document.getElementById("release-notes-close").addEventListener("click", () => dialog.close());
})();
