import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Home() {
  return (
    <main>
      <Helmet>
        <title>Trailhead - Find hikes near you</title>
        <meta name="description" content="Discover trails rated by local hikers." />
      </Helmet>
      <h1>Find your next hike</h1>
      <Link to="/about">About Trailhead</Link>
    </main>
  );
}
