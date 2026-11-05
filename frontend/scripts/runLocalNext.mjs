import { createRequire } from "node:module";
import { isIP } from "node:net";

const [command, ...args] = process.argv.slice(2);
if (!["dev", "start"].includes(command)) throw new Error("dev 또는 start를 지정하세요.");
for (let index = 0; index < args.length; index++) {
  const flag = args[index];
  if (["--hostname", "-H"].includes(flag) || flag.startsWith("--hostname=") || flag.startsWith("-H")) {
    const host = flag.includes("=") ? flag.slice(flag.indexOf("=") + 1) : flag.length > 2 && flag.startsWith("-H") ? flag.slice(2) : args[++index];
    const local = host === "localhost" || host === "::1" || (isIP(host || "") === 4 && host.startsWith("127."));
    if (!local) throw new Error("로컬 시연은 루프백 hostname만 허용합니다.");
  }
}
const nextCli = createRequire(import.meta.url).resolve("next/dist/bin/next");
process.argv = [process.execPath, nextCli, command, ...(command === "dev" ? ["--webpack"] : []), ...args, "--hostname", "127.0.0.1"];
await import(nextCli);
