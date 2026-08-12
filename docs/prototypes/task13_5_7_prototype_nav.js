(() => {
  const links = [...document.querySelectorAll("[data-section-link]")];
  if (!links.length) return;

  const availableSections = new Set(links.map((link) => link.dataset.sectionLink));
  const defaultSection = document.body.dataset.defaultSection || links[0].dataset.sectionLink;

  const setActiveSection = (sectionName) => {
    if (!availableSections.has(sectionName)) return;
    links.forEach((link) => {
      const active = link.dataset.sectionLink === sectionName;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  };

  const syncFromHash = () => {
    const sectionName = window.location.hash.slice(1) || defaultSection;
    setActiveSection(sectionName);
  };

  links.forEach((link) => {
    link.addEventListener("click", () => {
      const sectionName = link.dataset.sectionLink;
      setActiveSection(sectionName);
      if (!link.getAttribute("href")) {
        window.history.replaceState(null, "", `#${sectionName}`);
      }
    });
  });
  window.addEventListener("hashchange", syncFromHash);
  syncFromHash();
})();
