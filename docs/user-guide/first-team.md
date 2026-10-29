# Your first team

**Goal:** an Agent reads a short brief and answers in a Conversation. Have a working
[model connection](models.md) ready. You do not need a Sandbox.

## 1. Open a world

The welcome screen offers **Start Tutorial**, **Choose a workspace** and
**Start blank**. Choose **Start blank** to follow this exercise. If you already
have cards, use an empty area of the canvas. Choosing a workspace instead places
one or more selected blueprints; it does not send a task to a model.

## 2. Collect three cards

Open **Pack & Card Library → Packs** and open **Core essentials**. Open its wrapper
to browse the collected cards. Add **Text file**, **Agent** and **Conversation** to the
same deck and activate that deck. Click or drag each card from the bottom tray
to place one instance on the canvas.

Opening a Pack collects its reusable entries. Adding one to a Deck makes it
available in the tray. Placing it creates an instance in the World.

## 3. Write the brief

Open Text file. Hover over its title, choose the pencil (**Rename**) and name it
**Workshop brief**. Enter the text below, then choose **Save text**:

> We are planning a two-hour community workshop. We need an agenda, a preparation checklist and a short invitation.

Open the Agent's settings. Choose your model and give it the instruction:
**Help me plan this workshop. Ask before making assumptions.**

## 4. Grant access

Drag from an edge port of the Agent to Text and choose **Read**. Grant the
relationship. Read access does not allow the Agent to change the brief.

Connect the Agent to Conversation and choose **Participate**. In the Conversation,
check **Participants**. If your Agent is missing, choose **Add**, select it and
choose **Add selected**. Click that participant or mention it with **@** to
address your message.

![Connecting cards and choosing a relationship](../assets/demos/connect-cards.gif)

Placing cards next to each other, grouping them or sticking them together does
not grant access. Inspect a connection before granting more permissions.

## 5. Send the task and check the result

In Conversation, send:

> Read the Workshop brief and suggest a two-hour agenda. List questions you need me to answer.

The session shows the reply and tool activity. Check that the Agent uses your
brief and respects its two-hour limit. A connected model can still make mistakes;
review the result. If it cannot reply, check [models](models.md) and
[troubleshooting](troubleshooting.md).

Edit and save the brief, then ask for a revised agenda. Removing the Read
connection prevents future access through that connection; it does not erase
information already read or undo completed work.

Continue with [cards and connections](canvas.md) or [Packs and cards](plugins.md).
