const contacts = [
  { label: "GitHub", url: "https://github.com/asha-rao" },
  { label: "Email", url: "mailto:asha@example.com" },
];

export default function ContactRail() {
  return (
    <aside>
      {contacts.map((contact) => (
        <a href={contact.url} key={contact.url} aria-label={contact.label}>{contact.label}</a>
      ))}
    </aside>
  );
}
