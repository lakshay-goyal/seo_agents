---
name: explore-state-management
description: Find state and data-fetching libraries and where page content depends on client state.
---

State libraries are neutral for SEO unless page content only exists in client
state. Record which store/query layer is used and where content components read
from it; server-prefetching (dehydrate, initialData, SSR stores) means content is
available in the initial HTML.

```json profile
{
  "name": "state-management",
  "signals": [
    {"id": "store", "label": "Global store", "regex": "configureStore\\s*\\(|createStore\\s*\\(|createSlice\\s*\\(|\\bcreate\\s*\\(\\s*\\(set|defineStore\\s*\\(|new\\s+Vuex\\.Store|createPinia\\s*\\(|\\batom\\s*\\(|makeAutoObservable|writable\\s*\\(|createSignal\\s*\\(|NgRx|StoreModule\\.forRoot", "block": true},
    {"id": "query-client", "label": "Data-fetching cache layer", "regex": "\\buseQuery\\s*\\(|\\buseSWR\\s*\\(|new\\s+QueryClient\\s*\\(|new\\s+ApolloClient\\s*\\(|createApi\\s*\\(|useFetch\\s*\\(|useAsyncData\\s*\\(", "block": true},
    {"id": "server-prefetch", "label": "Server prefetch / dehydrated state", "regex": "dehydrate\\s*\\(|HydrationBoundary|initialData\\s*:|fallback\\s*:\\s*\\{|getDataFromTree|prefetchQuery\\s*\\(|TransferState"},
    {"id": "context-provider", "label": "Context provider wrapping the app", "regex": "createContext\\s*\\(|\\bprovide\\s*\\(\\s*['\"\\w]"}
  ],
  "gaps": [],
  "edit_guidance": "When page copy is read from a client store or client query, prefetch it on the server/build (dehydrate/initialData/useAsyncData/load) so it is part of the initial HTML."
}
```
