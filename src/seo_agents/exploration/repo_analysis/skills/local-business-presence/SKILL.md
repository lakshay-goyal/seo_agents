---
name: explore-local-business-presence
description: Find NAP (name, address, phone), LocalBusiness schema, maps embeds, opening hours and location pages.
---

Local SEO needs consistent NAP across the site and external listings,
LocalBusiness (or subtype) schema with address/geo/openingHours, a contact page
and per-location pages. Record every phone/address/email occurrence with its
location so inconsistencies are visible.

```json profile
{
  "name": "local-business-presence",
  "signals": [
    {"id": "local-schema", "label": "LocalBusiness-type schema", "regex": "['\"]@type['\"]\\s*:\\s*['\"](LocalBusiness|Restaurant|Store|MedicalBusiness|ProfessionalService|LegalService|Dentist|AutoRepair|HomeAndConstructionBusiness|FinancialService|HealthAndBeautyBusiness|LodgingBusiness|FoodEstablishment)['\"]", "value_group": 1},
    {"id": "postal-address", "label": "PostalAddress / address fields", "regex": "PostalAddress|streetAddress|addressLocality|postalCode|<address\\b"},
    {"id": "phone", "label": "Phone number", "regex": "tel:[+\\d\\s().-]{6,}|['\"]telephone['\"]\\s*:|\\+\\d{1,3}[\\s.-]?\\(?\\d{2,4}\\)?[\\s.-]?\\d{3,4}[\\s.-]?\\d{3,4}"},
    {"id": "email", "label": "Contact email", "regex": "mailto:[\\w.+-]+@[\\w-]+\\.[\\w.]+|['\"]email['\"]\\s*:\\s*['\"][\\w.+-]+@"},
    {"id": "map-embed", "label": "Map embed / directions link", "regex": "google\\.com/maps|maps\\.google|goo\\.gl/maps|maps\\.app\\.goo\\.gl|mapbox|leaflet|openstreetmap", "ignore_case": true},
    {"id": "opening-hours", "label": "Opening hours", "regex": "openingHours|OpeningHoursSpecification|opening\\s*hours|business\\s*hours", "ignore_case": true},
    {"id": "geo", "label": "Geo coordinates", "regex": "GeoCoordinates|['\"]latitude['\"]\\s*:|geo\\.position|ICBM"},
    {"id": "contact-or-location-page", "label": "Contact/location page", "regex": "(?:^|/)(?:contact|contact-us|locations?|stores?|find-us|visit)(?:/|\\.|$)", "files_only": true}
  ],
  "gaps": [
    {"id": "no-local-schema", "label": "Address/phone present but no LocalBusiness schema", "when_none_of": ["local-schema"], "only_if_any": ["postal-address", "map-embed", "opening-hours"]}
  ],
  "edit_guidance": "If the business serves a physical area, add LocalBusiness (most specific subtype) JSON-LD with the exact NAP used in the footer/contact page, geo and openingHoursSpecification; keep NAP text identical everywhere; create one page per location."
}
```
