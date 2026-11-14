// Demo sign-in page: stores the chosen role and name, then opens the right page.
// There is no real authentication here yet; university single sign-on would replace this file.
const NAME_KEY = "rewind.studentName";
const ROLE_KEY = "rewind.role";
const qs = location.search;
const input = document.querySelector("#name");
try { input.value = localStorage.getItem(NAME_KEY) || ""; } catch {}

function enter(role, page) {
  try {
    localStorage.setItem(ROLE_KEY, role);
    localStorage.setItem(NAME_KEY, input.value.trim() || "Guest");
  } catch {}
  location.href = page + qs;
}
document.querySelector("#as-student").addEventListener("click", () => enter("student", "index.html"));
document.querySelector("#as-lecturer").addEventListener("click", () => enter("lecturer", "lecturer.html"));
input.addEventListener("keydown", (e) => { if (e.key === "Enter") enter("student", "index.html"); });
