/**
 * Guide — what everything here is for, in the order you'd need it.
 *
 * The owner, after a reply from me full of statistical machinery:
 * *"what do you think I can understand about this? I'm back to
 * square one."* He has said a version of that four times now. The
 * product had grown to twenty-odd pages with no entry point, so
 * finding the right one meant opening three.
 *
 * ## Why the contents live here and not in a document
 *
 * A manual in a file rots. This one is a list beside the router,
 * and `tests/webui/test_guide.py` fails when a page exists with no
 * entry and when an entry points at a page that does not. So the
 * guide cannot quietly fall behind the product: adding a page
 * without explaining it breaks the build.
 *
 * ## The rules for the words
 *
 * No part of this may name a statistic, a file, a table or a
 * measurement's internal name. Every line answers "what is this
 * for" and "when would I open it", in the words he would use. The
 * jargon audit runs over this page like any other, and it is the
 * page where a failure matters most.
 */

import { esc } from './components.js';

/**
 * The first ten minutes, for someone who has just opened this.
 *
 * Three steps, in order, each ending somewhere real. Not a tour
 * of the features — a thing to actually do.
 */
const START = [
  {
    step: 'See what already works',
    href: '#/research',
    link: 'Research → What works',
    body: `Everything the system has worked out about your videos
      and other pianists'. Six patterns hold true both for you and
      for other creators independently — those are the strongest
      thing here. Start by reading those.`,
  },
  {
    step: 'Write something from it',
    href: '#/make',
    link: 'Make → Write something',
    body: `Ideas, opening text and captions, built only from what
      the evidence says — never from a general sense of what does
      well online. Everything it produces is kept, so you can come
      back to it.`,
  },
  {
    step: 'Check it before you post',
    href: '#/make?on=check',
    link: 'Make → Check a draft',
    body: `Upload the video you are about to post. It measures the
      same things it measures on everyone else and tells you where
      your draft sits. It never sees your account, and nothing is
      saved as a publication.`,
  },
];

/**
 * Every page, grouped by the question it answers.
 *
 * `href` has to be a route the app actually has — the test
 * checks. `when` is the part people skip to, so it is the part
 * that has to be concrete.
 */
const SECTIONS = [
  {
    name: 'Day to day',
    blurb: 'The four things in the sidebar, which is most of it.',
    pages: [
      { href: '#/', name: 'Home',
        what: 'How your recent videos did, and anything that needs you.',
        when: 'First thing. If nothing here surprises you, carry on.' },
      { href: '#/research', name: 'Research',
        what: `Eight views of what the evidence says — what works,
          whether you are doing it, how your videos look, your
          openings, where people stop watching, you against other
          creators, pairs of your own videos, when you post and
          how long they are.`,
        when: 'When you want to know what is true rather than what to make.' },
      { href: '#/make', name: 'Make',
        what: `Five tools for producing the next video: write
          something, check a draft, browse openings that worked,
          everything you have kept, and what happened to the ones
          you posted.`,
        when: 'When you are about to film or post.' },
      { href: '#/ask', name: 'Ask',
        what: `A chat that can only answer from what has been
          measured here. If it does not know, it says so rather
          than guessing.`,
        when: `When your question does not fit a page. "What should
          I change about my captions?" is a good one.` },
      { href: '#/collect', name: 'Collect data',
        what: `Everything the system can go and fetch for itself,
          with what each costs before you press it. The top of the
          page says where the evidence is thin.`,
        when: 'When a finding feels thin, or you want more creators studied.' },
    ],
  },
  {
    name: 'Inside Research',
    blurb: 'Each of these is a tab, not a separate place to remember.',
    pages: [
      { href: '#/views', name: 'What works',
        what: `Which things went together with doing better — on
          your account, on other creators', and which hold on both.
          Both sides is the strongest claim here.`,
        when: 'The first page to read, and the one to re-read monthly.' },
      { href: '#/following', name: 'Am I doing it?',
        what: `Your recent posts measured against those same
          patterns, so you can see where you have drifted.`,
        when: 'Before a run of filming.' },
      { href: '#/look', name: 'How my videos look',
        what: 'Framing, light, colour and on-screen text across your work.',
        when: 'When deciding how to shoot something.' },
      { href: '#/hooks', name: 'My openings',
        what: 'What the first seconds of your videos actually do.',
        when: 'When your views are dying early.' },
      { href: '#/hooks/retention', name: 'Staying watched',
        what: `Where people stop watching, for the videos where
          that can be measured.`,
        when: 'When you want to know if the problem is the start or the middle.' },
      { href: '#/research?on=others', name: 'Against other creators',
        what: `You beside a chosen group, with everyone scored
          against their own average so follower counts do not
          decide it.`,
        when: 'When you want to know if something is you or the niche.' },
      { href: '#/research?on=when', name: 'When you post',
        what: `Your hours and weekdays against how each video did.
          Your local time. It cannot tell you where your viewers
          are — only the platforms know that.`,
        when: 'When you are wondering whether timing matters. Mostly it does not.' },
      { href: '#/howlong', name: 'How long',
        what: `Which video lengths went with doing better, in
          bands. Asked this way because the ordinary test can only
          see "longer is better" or "shorter is better", and a
          best length in the middle cancels to nothing under it.`,
        when: 'When you are deciding how much to keep in.' },
      { href: '#/research?on=pairs', name: 'Two at a time',
        what: `Pairs of your own videos that should have done the
          same and did not — same length, same platform, same kind
          of piece.`,
        when: 'When you want the cleanest comparison available.' },
    ],
  },
  {
    name: 'Inside Make',
    blurb: '',
    pages: [
      { href: '#/write', name: 'Write something',
        what: `Ideas, opening text and captions. It is shown the
          evidence first and may only build on that, and you can
          read exactly what it was shown before you spend anything.`,
        when: 'When you need something to film and want it grounded.' },
      { href: '#/before', name: 'Check a draft',
        what: `Measure a video you have not posted against what
          works. Nothing is uploaded to any platform and no
          publication is created.`,
        when: 'The last thing before posting.' },
      { href: '#/openings', name: 'Openings that worked',
        what: `Yours and other people's, each ranked by how far it
          beat that creator's own usual — so a small account's
          breakout sits beside a big one's. Every one links to the
          actual post.`,
        when: 'When you want to read twenty and find a shape you like.' },
      { href: '#/make?on=kept', name: 'Kept',
        what: `Everything that has ever been generated for you.
          Mark one as posted and the system can later tell you how
          it did.`,
        when: 'Whenever. Nothing is lost on a refresh.' },
      { href: '#/make?on=worked', name: 'Did it work?',
        what: `The suggestions you marked as posted, each measured
          against your usual. It is honest that you chose which to
          use, so a good result proves less than it looks.`,
        when: 'After you have posted a few.' },
    ],
  },
  {
    name: 'Your videos and your files',
    blurb: '',
    pages: [
      { href: '#/library', name: 'All my videos',
        what: 'Everything you have published, with how each did.',
        when: 'When you are looking for one specific video.' },
      { href: '#/next', name: 'What to try next',
        what: 'Gaps between what works and what you have been doing.',
        when: 'When you want a shortlist rather than a page of evidence.' },
      { href: '#/review/masters', name: 'Match my files',
        what: `Links the video files on your own machine to the
          posts they became, where it is not certain.`,
        when: 'When it says something is waiting for you.' },
      { href: '#/review/pairs', name: 'Same video, both platforms',
        what: `Confirms when a post on one platform is the same
          video as a post on the other.`,
        when: 'When it says something is waiting for you.' },
      { href: '#/review/labels', name: 'Check uncertain labels',
        what: `Where the system read something off a video and is
          not sure. Your answer teaches it.`,
        when: `When you have five spare minutes — this is the
          highest-value small job in the product.` },
    ],
  },
  {
    name: 'Other creators',
    blurb: 'Who is being studied, and how to add more.',
    pages: [
      { href: '#/creators', name: 'Creators we study',
        what: 'Everyone in the set, with how much is known about each.',
        when: 'When you want to know whose videos a finding rests on.' },
      { href: '#/discover', name: 'Find creators',
        what: 'Searches for pianists we are not already following.',
        when: 'When you want more people in the set and have no names.' },
      { href: '#/collections', name: 'Groups of creators',
        what: `Named groups you can compare yourself against — for
          instance only the ones near your own size.`,
        when: 'When "everyone" is the wrong comparison.' },
    ],
  },
  {
    name: 'Reference',
    blurb: 'For when a number looks wrong.',
    pages: [
      { href: '#/glossary', name: 'What everything means',
        what: 'Plain-English definitions of every word used here.',
        when: 'Any time a page uses a word it should not have.' },
      { href: '#/features', name: 'Everything we can measure',
        what: `Every measurement, whether it is reliable, and how
          it was checked.`,
        when: 'When you want to know whether to trust a finding.' },
      { href: '#/competitors', name: 'What we cannot do yet',
        what: 'The honest list of gaps and why each one is still open.',
        when: 'Before asking for something that may be impossible.' },
      { href: '#/ai', name: 'AI models and spending',
        what: 'What has been spent, on what, and the caps you have set.',
        when: 'Monthly, or when something refuses to run.' },
      { href: '#/settings', name: 'Connections',
        what: 'Which accounts are linked and whether they still work.',
        when: 'When collection stops bringing anything new in.' },
    ],
  },
];

/** Things worth knowing once, that no single page says. */
const RULES = [
  {
    name: 'Nothing here says one thing causes another',
    body: `Every result means "videos with this also tended to do
      better". It does not mean doing it will make your next video
      do better. Only a proper experiment could show that, and one
      has not finished. The product refuses to word it any other
      way, even when asked directly.`,
  },
  {
    name: 'Every video is judged against its own creator',
    body: `A video is never compared to a star's view count. It is
      compared to the other videos on the same account, so a
      400-follower pianist's breakout counts the same as a
      2-million-follower one's. Otherwise every finding would just
      be "have more followers".`,
  },
  {
    name: 'Holding on both sides is the bar',
    body: `A pattern seen only on your account describes your
      account. The same pattern found independently on hundreds of
      other creators is worth acting on. Pages say which they are,
      and six currently hold on both.`,
  },
  {
    name: 'Nothing spends money without you pressing it',
    body: `Anything that costs says what it costs first. The jobs
      that run on their own are only ever the free ones. There is
      a monthly ceiling you set, and it stops rather than
      overspending.`,
  },
];

export const guide = {
  title: 'Guide',
  async render(root) {
    root.innerHTML = `
      <h1>Guide</h1>
      <p class="lede">What everything here is for. Nothing on this
        page costs anything or changes anything.</p>

      <section class="card">
        <h2>If you do three things</h2>
        <ol class="plain">${START.map((s) => `<li>
          <h3>${esc(s.step)}</h3>
          <p>${esc(s.body.replace(/\s+/g, ' ').trim())}</p>
          <p><a class="btn" href="${esc(s.href)}">${esc(s.link)}</a></p>
        </li>`).join('')}</ol>
      </section>

      <section class="card">
        <h2>Worth knowing once</h2>
        ${RULES.map((r) => `<div class="gl-entry">
          <h3>${esc(r.name)}</h3>
          <p>${esc(r.body.replace(/\s+/g, ' ').trim())}</p>
        </div>`).join('')}
      </section>

      ${SECTIONS.map(section).join('')}`;
  },
};

function section(s) {
  return `<section class="card">
    <h2>${esc(s.name)}</h2>
    ${s.blurb ? `<p class="muted">${esc(s.blurb)}</p>` : ''}
    <table class="grid"><tbody>${s.pages.map((p) => `<tr>
      <td><a href="${esc(p.href)}"><b>${esc(p.name)}</b></a></td>
      <td>${esc(p.what.replace(/\s+/g, ' ').trim())}
        <br><span class="muted">${esc(p.when)}</span></td>
    </tr>`).join('')}</tbody></table>
  </section>`;
}

//: Exported for the test that keeps this honest. See the note at
//: the top: a page with no entry here, or an entry pointing at no
//: page, fails the build rather than quietly misleading him.
export const GUIDE_LINKS = SECTIONS.flatMap(
  (s) => s.pages.map((p) => p.href),
).concat(START.map((s) => s.href));
