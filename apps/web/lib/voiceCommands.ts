/** Kept for older imports: the spoken controls now live in voiceIntents.ts. */
import { matchIntent, normalise, type VoiceIntent } from "./voiceIntents";

export type VoiceCommand = Extract<VoiceIntent, "emergency" | "repeat" | "stop" | "home" | "read">;
export { normalise };

export function matchCommand(text: string): VoiceCommand | null {
  const i = matchIntent(text);
  return i === "emergency" || i === "repeat" || i === "stop" || i === "home" || i === "read" ? i : null;
}
