<script setup>
const route = useRoute();
</script>

<template>
  <main>
    <h1>Course {{ route.params.id }}</h1>
  </main>
</template>
