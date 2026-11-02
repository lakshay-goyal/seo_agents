import Hero from "@/components/Hero";

export const metadata = {
  title: "Home",
  description: "Welcome to Acme.",
  alternates: { canonical: "https://acme.test/" },
};

export default function Home() {
  return (
    <>
      <Hero title="Build faster with Acme" />
      <p>Tools for teams that ship every day.</p>
      <img src="/hero.png" alt="Acme dashboard" width={1200} height={600} />
    </>
  );
}
