# Publishing: a private page in the host (Artifact)

An optional route: only at the user's request or with their consent to a proposal (e.g. when the reader has no access to the repo).
**Without the user's decision do not publish anything.** The route applies to a host that has the Artifact tool; in a session
without it use [publish-static.md](publish-static.md) and do not assume Artifact exists.

## Contract (from the host's documentation, not from memory)

Before the first publication check the current shape of the call in the tool's documentation; below only what matters for the method.

- The page is **private**: the account owner sees it. The reader will see it only after the owner **shares it**
  (the share button in the host). Sharing is managed by the owner, not the agent.
- The content is one self-contained HTML file (from `assets/template.html`).
- You pass images as **files published next to the page** (the `files` parameter), not as `data:` in HTML. Paths in the HTML
  are relative and match the names of the passed files.
- **The same file path = the same address.** Updating the page means publishing again under the same path.

## Steps

1. Assemble the page and images the same way as for the static route; check locally (both themes, 390 px).
2. Ask the user for consent to publish, if it was not given explicitly.
3. Publish the HTML file together with the images in the `files` parameter.
4. Give the user the address from the tool's response and say: the reader will see the page only after the owner shares it;
   until then it is private.
5. Do not write the page address to core files or public documents; if the project wants to keep it, write it only
   in its own documentation, with the user's consent.

## Updating

After the rules change publish again under the **same file path**, so the address does not change; put the new
date and version in the header.

## Limitations

The route has no automatic test. Verify it by hand (a private publication and viewing the result) or report it as unverified.
