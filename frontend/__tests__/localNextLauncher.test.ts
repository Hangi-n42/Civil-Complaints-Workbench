import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../", import.meta.url));
const script = fileURLToPath(new URL("../scripts/runLocalNext.mjs", import.meta.url));

describe("로컬 화면 실행 경계", () => {
  it.each([
    ["dev", "--hostname", "0.0.0.0"],
    ["dev", "--hostname=0.0.0.0"],
    ["dev", "-H", "::"],
    ["dev", "-H0.0.0.0"],
    ["start", "--hostname", "192.0.2.1"],
  ])("서버 시작 전에 비로컬 hostname 인자를 거절한다: %j", (...args) => {
    const result = spawnSync(process.execPath, [script, ...args], { cwd: root, encoding: "utf8", timeout: 5000 });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("루프백 hostname만 허용");
    expect(result.stdout).not.toContain("Local:");
  });

  it.each(["dev", "start"])("%s 명령은 실제 Next CLI까지 전달된다", (command) => {
    const result = spawnSync(process.execPath, [script, command, "--help"], { cwd: root, encoding: "utf8", timeout: 5000 });
    expect(result.status).toBe(0);
    expect(result.stdout).toContain(`Usage: next ${command}`);
  });
});
