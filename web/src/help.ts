/** The video guides (docs/VIDEOS.md), published as a YouTube playlist. Change the link here only. */
export const VIDEO_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLR4GI8Rc_Nl4";

/** The dashboard's version, from web/package.json (set at build time in vite.config.ts). */
export const APP_VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

export interface VideoGuide {
  title: string;
  length: string;
  about: string;
}

export const VIDEO_GROUPS: { heading: string; videos: VideoGuide[] }[] = [
  {
    heading: "Start here",
    videos: [{ title: "A quick tour", length: "3 min", about: "The home page, a PC's page, lock now, extra time, messages and closing an app." }],
  },
  {
    heading: "Setting up (once, in this order)",
    videos: [
      { title: "Setting up the server on Render", length: "3 min", about: "Putting Kidputer Control online and turning on two-factor sign-in." },
      { title: "Installing on your child's PC", length: "3 min", about: "Adding a PC, the installer, and setting the bedtime and PIN." },
      { title: "Testing on a real PC", length: "3 min", about: "The one-time checklist to run on the PC before relying on it." },
    ],
  },
  {
    heading: "Everyday use",
    videos: [
      { title: "Using it from your phone", length: "1 min", about: "Home screen, lock, extra time, messages and phone alerts." },
      { title: "Parents, passkeys and sign-in", length: "1 min", about: "Inviting the other parent and signing in with your fingerprint or face." },
      { title: "App rules, usage, files and change alerts", length: "3 min", about: "When apps may be used, screen time, drives and folders." },
      { title: "Virus protection and Windows updates", length: "2 min", about: "The Security tab: threats, scans, updates and the restart at bedtime." },
    ],
  },
  {
    heading: "For developers",
    videos: [{ title: "How it's built, tested and released", length: "2 min", about: "The three parts, branches, tests, and re-recording these videos." }],
  },
];
