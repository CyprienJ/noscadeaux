const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const template = fs.readFileSync(path.join(__dirname, "../templates/groups/group_invitations.html"), "utf8");
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/{{\s*(\w+)(?:\|escapejs)?\s*}}/g, (_, name) => name)
    .replace(/{{ group.name\|escapejs }}/g, "Family");

function setup(navigator, canCopy = true) {
    const status = { textContent: "" };
    let selections = 0;
    const context = vm.createContext({
        navigator,
        document: {
            getElementById: (id) => id === "shareStatus" ? status : { select() { selections += 1; } },
            execCommand: () => canCopy,
        },
    });
    vm.runInContext(script, context);
    return { context, status, selections: () => selections };
}

test("copies with the clipboard API", async () => {
    let copied;
    const app = setup({ clipboard: { writeText: async (value) => { copied = value; } } });
    await app.context.copyInvitation();
    assert.equal(copied, "invitation_url");
    assert.equal(app.status.textContent, "link_copied");
});

test("unsupported sharing falls back to selecting and copying", async () => {
    const app = setup({});
    await app.context.shareInvitation();
    assert.equal(app.selections(), 1);
    assert.equal(app.status.textContent, "link_copied");
});

test("blocked clipboard and legacy copy offer manual copying", async () => {
    const app = setup({ clipboard: { writeText: async () => { throw new Error("Denied"); } } }, false);
    await app.context.copyInvitation();
    assert.equal(app.status.textContent, "manual_copy");
    assert.equal(app.selections(), 1);
});

test("native sharing uses the private link", async () => {
    let shared;
    const app = setup({ share: async (value) => { shared = value; } });
    await app.context.shareInvitation();
    assert.equal(shared.url, "invitation_url");
    assert.equal(shared.title, "Family");
    assert.equal(app.status.textContent, "link_shared");
});

test("cancelling native sharing does not claim success or copy", async () => {
    const app = setup({ share: async () => { throw { name: "AbortError" }; } });
    await app.context.shareInvitation();
    assert.equal(app.status.textContent, "");
    assert.equal(app.selections(), 0);
});

test("a native sharing failure falls back to copy", async () => {
    const app = setup({ share: async () => { throw new Error("Unavailable"); } });
    await app.context.shareInvitation();
    assert.equal(app.status.textContent, "link_copied");
});
