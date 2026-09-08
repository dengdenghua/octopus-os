import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readNASTextDocument,
  saveNASTextDocument,
  diffNASTextDocument,
  supportsNASTextEditing,
  storageFileResourceId,
  NASRequestError,
  nasTextEditError,
} from "./api";

const resource = storageFileResourceId("a".repeat(64), "notes.md")!;
const revision = "b".repeat(64);
const document = {
  resource_id: resource,
  text: "完整文本",
  revision,
  size: 12,
  encoding: "utf-8",
  complete: true,
};
afterEach(() => vi.unstubAllGlobals());

describe("Storage text protocol", () => {
  it("enables editing by capability for either presentation", () => {
    for (const role of ["embedded", "external"]) {
      expect(
        supportsNASTextEditing({
          service: "echo-storage",
          version: "1",
          role,
          capabilities: ["text-edit.v1"],
        }),
      ).toBe(true);
      expect(
        supportsNASTextEditing({
          service: "echo-storage",
          version: "1",
          role,
          capabilities: [],
        }),
      ).toBe(false);
    }
  });

  it("fetches complete text independently of the bounded preview", async () => {
    const text = "a".repeat(250_000) + "END";
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...document,
          text,
          size: text.length,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    expect((await readNASTextDocument(resource)).text).toBe(text);
    expect(fetch.mock.calls[0]![0]).toContain(
      `/files/${encodeURIComponent(resource)}/text`,
    );
  });

  it("rejects a truncated or foreign document before editing", async () => {
    for (const invalid of [
      { ...document, complete: false },
      { ...document, size: 1 },
      { ...document, resource_id: "other" },
    ]) {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(JSON.stringify(invalid))),
      );
      await expect(readNASTextDocument(resource)).rejects.toThrow(
        "未返回完整文本",
      );
    }
  });

  it("sends resource identity and expected revision for save and diff", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response(JSON.stringify(document))),
      );
    vi.stubGlobal("fetch", fetch);
    await saveNASTextDocument(resource, document.text, revision);
    expect(fetch.mock.calls[0]![1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({
        text: document.text,
        expected_revision: revision,
      }),
    });
    await diffNASTextDocument(resource, "draft", revision);
    expect(fetch.mock.calls[1]![0]).toContain("/diff");
    expect(fetch.mock.calls[1]![1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ text: "draft", expected_revision: revision }),
    });
  });

  it("does not fall back to appliance uploads or retry conflicts", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("revision_conflict", { status: 409 }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      saveNASTextDocument(resource, "draft", revision),
    ).rejects.toMatchObject({ status: 409 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(nasTextEditError(new NASRequestError("/text", 409))).toContain(
      "草稿已保留",
    );
  });
});
