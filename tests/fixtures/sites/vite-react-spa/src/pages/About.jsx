import { Helmet } from "react-helmet-async";

export default function About() {
  return (
    <main>
      <Helmet>
        <title>About Trailhead</title>
      </Helmet>
      <h1>About</h1>
      <img src="/team.jpg" alt="The Trailhead team on a summit" />
    </main>
  );
}
