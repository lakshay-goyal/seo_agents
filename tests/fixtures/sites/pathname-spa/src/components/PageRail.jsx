const pages = [
  { label: "Home", href: "/" },
  { label: "About", href: "/about" },
  { label: "Bookshelf", href: "/bookshelf" },
  { label: "Blogs", href: "/blog" },
];

export default function PageRail({ path }) {
  return (
    <nav aria-label="Pages">
      {pages.map((page) => (
        <a href={page.href} key={page.href} aria-current={path === page.href ? "page" : undefined}>
          <span>{page.label}</span>
        </a>
      ))}
    </nav>
  );
}
