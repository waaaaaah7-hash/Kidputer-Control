import { Link } from "react-router-dom";
import { APP_VERSION, VIDEO_GROUPS, VIDEO_PLAYLIST_URL } from "../help";
import { Icon } from "../icons";
import { Card } from "../ui";

/** Help: the video guides on YouTube, and answers to the questions parents ask most. */
export default function Help() {
  return (
    <>
      <div className="page-head">
        <h1>Help</h1>
      </div>
      <div className="columns">
        <div className="stack">
          <Card title="Video guides">
            <p>Short narrated videos that show every part of Kidputer Control, step by step. Captions are on screen too.</p>
            <a className="btn btn-primary help-watch" href={VIDEO_PLAYLIST_URL} target="_blank" rel="noopener noreferrer" data-testid="playlist-link">
              <Icon name="play" /> Watch on YouTube
            </a>
            <div className="stack help-videos">
              {VIDEO_GROUPS.map((g) => (
                <section key={g.heading}>
                  <h3>{g.heading}</h3>
                  <ul className="list">
                    {g.videos.map((v) => (
                      <li key={v.title} className="list-row align-top">
                        <div className="grow">
                          <strong>{v.title}</strong>
                          <div className="muted small">{v.about}</div>
                        </div>
                        <span className="muted small nowrap">{v.length}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
            <p className="muted small">The videos are all in one playlist on YouTube, which opens in a new tab (or the YouTube app on a phone).</p>
          </Card>
        </div>
        <div className="stack">
          <Card title="Quick answers">
            <dl className="faq">
              <dt>The PC didn't lock at bedtime.</dt>
              <dd>
                Check the PC's card on the <Link to="/">home page</Link>: it should be online, with a bedtime set under <strong>Schedule and settings</strong>.
                Extra time also keeps it unlocked until it runs out. If it still doesn't lock, work through the real-PC checklist (in the video guides).
              </dd>
              <dt>It locked at the wrong time.</dt>
              <dd>The schedule follows the PC's time zone. Check it on the PC's page, under Schedule and settings.</dd>
              <dt>How do I get alerts on my phone?</dt>
              <dd>
                Open <Link to="/account/notifications">Notifications</Link> on your phone and tap <strong>Turn on for this device</strong>. On an iPhone, add
                the dashboard to your Home Screen first and open it from there.
              </dd>
              <dt>How do I add the other parent?</dt>
              <dd>
                On <Link to="/account">Parents and account</Link>, enter their email and send them the one-time link it shows.
              </dd>
              <dt>My child is locked out and I'm next to the PC.</dt>
              <dd>Type the lock-screen PIN on the PC: it unlocks it for an hour. Set the PIN under Schedule and settings.</dd>
              <dt>I lost my authenticator app.</dt>
              <dd>Sign in with a passkey if you added one. Otherwise, whoever runs the server can reset it (see the Render setup video).</dd>
            </dl>
          </Card>
          <p className="muted small">Kidputer Control {APP_VERSION}</p>
        </div>
      </div>
    </>
  );
}
