// Engine picker + health. Writes `engine` to the URL hash; other tools read ctx.engines.current().
import { h, btn, help, row } from "../panel.js";

export const id = "engines";
export const title = "Engines";

export async function init(ctx) {
  const list = h("div");
  ctx.panel.append(
    h("h2", {}, "Routing engines"),
    help("Each engine is one OSRM container from docker-compose.yml. Pick the one every tool should use."),
    list,
    row(btn("Re-check", render)),
    help("Start one with:  docker compose up -d <service>"),
  );

  async function render() {
    const engines = await ctx.engines.refresh();
    const cur = ctx.engines.current();
    list.replaceChildren(...engines.map((e) => h("label", { class: "row", style: "cursor:pointer" },
      h("input", { type: "radio", name: "engine", value: e.id, checked: e.id === cur, onChange: () => ctx.engines.set(e.id) }),
      h("span", { class: "sw", style: `background:${e.color}` }),
      h("span", {}, h("b", {}, e.id), " ", h("span", { class: e.healthy ? "pass" : "fail" }, e.healthy ? "up" : "down")),
      h("span", { class: "help", style: "margin:0 0 0 auto" }, e.url.replace("http://", "")),
      h("div", { class: "help", style: "flex-basis:100%;margin:-2px 0 4px 24px" }, `${e.title} · service ${e.compose_service}`),
    )));
  }
  ctx.engines.on(render);
  render();
  return { activate: render };
}
