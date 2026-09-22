document.addEventListener("click", (event) => {
  const button = event.target.closest(".json-copy");
  if (!button) {
    return;
  }

  const source = button.parentElement.querySelector(".json-copy-source");
  if (!source) {
    return;
  }

  const original = button.textContent;
  const restore = () => {
    button.textContent = original;
  };

  const copied = () => {
    button.textContent = "Copied";
    window.setTimeout(restore, 1400);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(source.value).then(copied, restore);
    return;
  }

  source.hidden = false;
  source.select();
  document.execCommand("copy");
  source.hidden = true;
  copied();
});
