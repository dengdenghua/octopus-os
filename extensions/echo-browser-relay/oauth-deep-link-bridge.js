(() => {
  "use strict";

  const core = globalThis.EchoMcpOAuthDeepLink;
  if (!core) return;

  document.addEventListener(
    "click",
    (event) => {
      const path = event.composedPath?.() || [];
      const anchor = path.find(
        (node) => node?.tagName === "A" && typeof node.href === "string",
      );
      const deepLinkURL = String(anchor?.href || "");
      const candidate = core.buildCallbackURL({
        sourceURL: window.location.href,
        deepLinkURL,
        backendBaseURL: "http://127.0.0.1:8000",
      });
      if (!candidate) return;

      // Prevent the operating system from launching WorkBuddy. The service
      // worker independently validates the source, state and callback before
      // returning a loopback URL; neither URL is written to logs.
      event.preventDefault();
      event.stopImmediatePropagation();
      void chrome.runtime
        .sendMessage({
          type: "echo.mcpOAuthDeepLink",
          deep_link_url: deepLinkURL,
        })
        .then((response) => {
          if (response?.ok && response.callback_url) {
            window.location.replace(response.callback_url);
          }
        })
        .catch(() => {});
    },
    true,
  );
})();

