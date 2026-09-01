import { nanoid } from "nanoid";

export function uuid(): string {
  // nanoid's URL alphabet also contains "-" and "_". Both are valid after
  // the first character in our backend thread-id contract, but not as the
  // first character. Prefixing keeps every locally created thread valid
  // across the realtime log and collaboration stores.
  return `t${nanoid()}`;
}
