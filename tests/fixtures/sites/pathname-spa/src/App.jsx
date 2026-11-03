import PageRail from "./components/PageRail.jsx";
import ContactRail from "./components/ContactRail.jsx";
import posts from "./store/data/posts.json";
import books from "./store/data/books.json";

function HomePage() {
  return <section><h2>Selected work</h2><p>Mobile and web products.</p></section>;
}

function AboutPage() {
  return <section><h2>About</h2><p>I build things for the web.</p></section>;
}

function BookshelfPage() {
  return <section><h2>Bookshelf</h2><ul>{books.map((book) => <li key={book.title}>{book.title}</li>)}</ul></section>;
}

function BlogsPage() {
  return <section><h2>Blog</h2>{posts.map((post) => <a key={post.slug} href={`/blog/${post.slug}`}>{post.title}</a>)}</section>;
}

function BlogPostPage({ slug }) {
  const post = posts.find((item) => item.slug === slug);
  return <article><h2>{post ? post.title : "Missing"}</h2></article>;
}

function App() {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  const page = path === "/" ? <HomePage />
    : path === "/about" ? <AboutPage />
    : path === "/bookshelf" ? <BookshelfPage />
    : path === "/blog" ? <BlogsPage />
    : path.startsWith("/blog/") ? <BlogPostPage slug={path.slice(6)} />
    : <div><h2>Page not found</h2><p>There is nothing at this address.</p></div>;

  return (
    <main>
      <PageRail path={path} />
      <header><a href="/">Asha Rao</a><p>Software Engineer</p></header>
      {page}
      <ContactRail />
    </main>
  );
}

export default App;
