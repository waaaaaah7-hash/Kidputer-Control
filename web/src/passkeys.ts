import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import { api, type Passkey } from "./api";
import { UserCancelled } from "./cancel";

/** Thrown when the parent closes the browser's passkey prompt: callers stay quiet rather than show an error. */
export class PasskeyCancelled extends UserCancelled {
  constructor() {
    super("Passkey prompt was cancelled");
  }
}

export function passkeysSupported(): boolean {
  return browserSupportsWebAuthn();
}

function rethrow(e: unknown): never {
  // NotAllowedError = the user dismissed the prompt, or it timed out.
  if (e instanceof Error && (e.name === "NotAllowedError" || e.name === "AbortError")) throw new PasskeyCancelled();
  throw e;
}

async function getAssertion(options: Record<string, unknown>) {
  try {
    return await startAuthentication({ optionsJSON: options as unknown as PublicKeyCredentialRequestOptionsJSON });
  } catch (e) {
    rethrow(e);
  }
}

export async function signInWithPasskey(): Promise<void> {
  const { challenge_id, options } = await api.passkeyLoginOptions();
  await api.passkeyLoginVerify(challenge_id, await getAssertion(options));
}

export async function confirmWithPasskey(): Promise<void> {
  const { challenge_id, options } = await api.passkeyReverifyOptions();
  await api.passkeyReverifyVerify(challenge_id, await getAssertion(options));
}

export async function addPasskey(name: string): Promise<Passkey> {
  const { challenge_id, options } = await api.passkeyRegisterOptions();
  let credential;
  try {
    credential = await startRegistration({ optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON });
  } catch (e) {
    rethrow(e);
  }
  return api.passkeyRegisterVerify(challenge_id, credential, name);
}
