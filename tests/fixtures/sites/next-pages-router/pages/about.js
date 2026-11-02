import Head from "next/head";

export default function About() {
  return (
    <>
      <Head>
        <title>About | Northwind</title>
        <meta name="description" content="Meet the roasters behind Northwind." />
      </Head>
      <h1>Our story</h1>
      <h2>The roastery</h2>
      <img src="/roastery.jpg" alt="Coffee roaster in our workshop" />
    </>
  );
}
