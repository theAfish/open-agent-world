# First task and recovery

The welcome screen starts with a goal and the existing starter workspaces. Select General assistant, Coding workspace, or Multi-Agent collaboration and choose **Open workspace**. The goal becomes an unsent conversation draft; opening a workspace never starts an Agent automatically. Templates remain Legion templates, so the existing Library and creator-pack workflow still apply.

The starter is instantiated as a Legion with a working layout. If the default model is missing, **Connect AI** opens before the workspace. Choose OpenAI, Anthropic, Gemini, Ollama or LM Studio, provide credentials if needed, retrieve the model list and select a model. Save settings to continue. Local service addresses refer to the backend machine. Use custom/manual setup for a proxy, a different address, TypeSafe, or a service that cannot list models.

Discovery is a read-only metadata request. It does not verify generation, billing, tool support, or every listed model's capabilities. Select a chat/tool-capable model; exact model IDs can still be entered in advanced settings. Discovery never saves credentials until Settings is saved, follows no redirects, bounds response size/time and does not forward a stored key to an edited destination without re-entry.

The canvas **New workspace** button reopens the chooser. Existing workspaces resume on reload; closing a workspace deliberately returns to the canvas. Canvas customization and the guided tutorial remain available.

Conversation and lifecycle surfaces use Queued / Working / Waiting / Completed / Failed / Stopped / Interrupted. Existing Stop and queued-message behavior is retained. A failed attempt shows recovery guidance. **Review and resend** copies a prior user message and attachments into the composer for review; it never automatically reruns tools. Completed external actions may already have taken effect. Model settings are accessible from failure guidance.

Desktop signing, optional automatic updates, publisher configuration and recovery are documented in [releasing.md](releasing.md). Help/diagnostics in PR #27 is complementary; its manual release-page link remains valid for unsigned/unconfigured builds.
