from job_radar.parse.html_to_md import html_to_markdown


JD_HTML = """
<html><body>
  <nav>Home | Jobs | About</nav>
  <main>
    <article>
      <h1>Senior Platform Engineer</h1>
      <p>We are hiring a senior engineer to help us build and operate the
      platform tooling that powers our product organization. You will work
      across Python services, CI/CD, and observability, and partner closely
      with application teams.</p>
      <h2>What you'll do</h2>
      <ul>
        <li>Design and ship platform services in Python.</li>
        <li>Own CI/CD pipelines and developer-experience improvements.</li>
        <li>Mentor engineers on production readiness and on-call practices.</li>
      </ul>
      <h2>Requirements</h2>
      <ul>
        <li>5+ years of backend Python experience.</li>
        <li>Strong background in distributed systems.</li>
        <li>Excellent written and verbal communication.</li>
      </ul>
    </article>
  </main>
  <footer>Privacy | Terms</footer>
</body></html>
"""


def test_keeps_body_and_bullets():
    md = html_to_markdown(JD_HTML)
    assert "Senior Platform Engineer" in md
    assert "Python" in md
    assert "5+ years of backend Python" in md
    assert "Design and ship platform services" in md


def test_drops_obvious_chrome():
    md = html_to_markdown(JD_HTML)
    # Footer boilerplate should not survive into the JD body.
    assert "Privacy | Terms" not in md


def test_empty_input_returns_empty():
    assert html_to_markdown("") == ""
