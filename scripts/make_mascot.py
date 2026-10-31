"""Draw Capy, Orbital's capybara mascot, as SVG: one base drawing plus a per-role accessory.

    uv run python scripts/make_mascot.py   # writes web/assets/capy-*.svg
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "assets"

FUR, FUR_DARK, SNOUT, INK, BLUSH = "#B27A4E", "#7A4E2E", "#D3A47A", "#2E1C12", "#F49A9A"

BASE = f"""
  <ellipse cx="100" cy="186" rx="58" ry="7" fill="#000" opacity=".12"/>
  <!-- body -->
  <path d="M42 150 C40 118 70 104 100 104 C132 104 162 118 160 152 C159 174 140 182 100 182 C60 182 43 172 42 150 Z"
        fill="{FUR}" stroke="{FUR_DARK}" stroke-width="3" stroke-linejoin="round"/>
  <path d="M70 160 C72 146 86 140 100 140 C114 140 128 146 130 160 C126 172 112 176 100 176 C88 176 74 172 70 160 Z"
        fill="{SNOUT}" opacity=".55"/>
  <!-- feet -->
  <rect x="58" y="170" width="22" height="14" rx="7" fill="{FUR_DARK}"/>
  <rect x="120" y="170" width="22" height="14" rx="7" fill="{FUR_DARK}"/>
  <!-- ears: small, set high on the corners like a real capybara -->
  <ellipse cx="62" cy="47" rx="8" ry="6.5" fill="{FUR_DARK}" transform="rotate(-20 62 47)"/>
  <ellipse cx="138" cy="47" rx="8" ry="6.5" fill="{FUR_DARK}" transform="rotate(20 138 47)"/>
  <ellipse cx="62" cy="48" rx="4" ry="3" fill="{BLUSH}" opacity=".7" transform="rotate(-20 62 48)"/>
  <ellipse cx="138" cy="48" rx="4" ry="3" fill="{BLUSH}" opacity=".7" transform="rotate(20 138 48)"/>
  <!-- head: boxy, a little wider at the muzzle -->
  <path d="M58 62 C58 46 76 40 100 40 C124 40 142 46 142 62 L146 108 C147 128 128 138 100 138 C72 138 53 128 54 108 Z"
        fill="{FUR}" stroke="{FUR_DARK}" stroke-width="3" stroke-linejoin="round"/>
  <!-- big flat muzzle -->
  <path d="M62 100 C62 86 78 82 100 82 C122 82 138 86 138 100 L138 112 C138 126 122 132 100 132 C78 132 62 126 62 112 Z"
        fill="{SNOUT}"/>
  <path d="M100 82 L100 92" stroke="{FUR_DARK}" stroke-width="2" stroke-linecap="round" opacity=".35"/>
  <ellipse cx="86" cy="96" rx="5" ry="3.2" fill="{INK}" transform="rotate(-12 86 96)"/>
  <ellipse cx="114" cy="96" rx="5" ry="3.2" fill="{INK}" transform="rotate(12 114 96)"/>
  <path d="M91 113 Q95.5 118.5 100 113 Q104.5 118.5 109 113" fill="none" stroke="{INK}" stroke-width="2.4" stroke-linecap="round"/>
  <!-- cheeks -->
  <ellipse cx="62" cy="96" rx="8" ry="5.5" fill="{BLUSH}" opacity=".6"/>
  <ellipse cx="138" cy="96" rx="8" ry="5.5" fill="{BLUSH}" opacity=".6"/>
  <!-- eyes: small and high, with the famous relaxed half-lid -->
  <g class="eyes">
    <ellipse cx="78" cy="68" rx="5" ry="5.5" fill="{INK}"/>
    <ellipse cx="122" cy="68" rx="5" ry="5.5" fill="{INK}"/>
    <circle cx="79.8" cy="67.5" r="1.6" fill="#fff"/>
    <circle cx="123.8" cy="67.5" r="1.6" fill="#fff"/>
    <path d="M72 64.5 Q78 61 84 64.5 L84 62 Q78 59.5 72 62 Z" fill="{FUR}"/>
    <path d="M116 64.5 Q122 61 128 64.5 L128 62 Q122 59.5 116 62 Z" fill="{FUR}"/>
    <path d="M72 64.5 Q78 61 84 64.5 M116 64.5 Q122 61 128 64.5" fill="none" stroke="{FUR_DARK}" stroke-width="1.8" stroke-linecap="round"/>
  </g>
"""

ACCESSORIES = {
    # the classic capybara-with-a-yuzu: calm authority
    "director": """
  <circle cx="100" cy="30" r="14" fill="#FFB02E" stroke="#E07B0A" stroke-width="2.5"/>
  <circle cx="95" cy="25" r="3" fill="#FFE29A" opacity=".9"/>
  <path d="M101 17 C104 9 114 8 118 12 C113 16 107 18 101 17 Z" fill="#5BBF6A" stroke="#2F8A3E" stroke-width="1.5"/>
""",
    # swim goggles pushed up on the forehead
    "water": """
  <path d="M58 52 C72 47 128 47 142 52" fill="none" stroke="#1E6FB8" stroke-width="5" stroke-linecap="round"/>
  <circle cx="85" cy="49" r="9.5" fill="#7DD3FC" stroke="#1E6FB8" stroke-width="3.5"/>
  <circle cx="115" cy="49" r="9.5" fill="#7DD3FC" stroke="#1E6FB8" stroke-width="3.5"/>
  <circle cx="82" cy="46" r="3" fill="#fff" opacity=".85"/>
  <circle cx="112" cy="46" r="3" fill="#fff" opacity=".85"/>
  <path d="M100 150 C94 160 94 166 100 168 C106 166 106 160 100 150 Z" fill="#38BDF8" stroke="#1E6FB8" stroke-width="2"/>
""",
    # firefighter helmet
    "fire": """
  <path d="M54 58 C54 30 76 20 100 20 C124 20 146 30 146 58 Z" fill="#E11D2E" stroke="#8F0F1B" stroke-width="3" stroke-linejoin="round"/>
  <path d="M44 58 L156 58 C156 64 150 67 144 67 L56 67 C50 67 44 64 44 58 Z" fill="#B3121F" stroke="#8F0F1B" stroke-width="2.5"/>
  <path d="M100 22 L100 56" stroke="#8F0F1B" stroke-width="3"/>
  <rect x="88" y="32" width="24" height="16" rx="4" fill="#FCD34D" stroke="#B45309" stroke-width="2"/>
  <path d="M100 35 L102.5 39.5 L107 40 L103.5 43 L104.5 47 L100 45 L95.5 47 L96.5 43 L93 40 L97.5 39.5 Z" fill="#B91C1C"/>
""",
    # round reading glasses: the careful one
    "reviewer": """
  <circle cx="78" cy="68" r="12" fill="#fff" fill-opacity=".18" stroke="#3B2A1F" stroke-width="3"/>
  <circle cx="122" cy="68" r="12" fill="#fff" fill-opacity=".18" stroke="#3B2A1F" stroke-width="3"/>
  <path d="M90 67 Q100 62 110 67" fill="none" stroke="#3B2A1F" stroke-width="3" stroke-linecap="round"/>
  <path d="M66 66 L58 62 M134 66 L142 62" stroke="#3B2A1F" stroke-width="3" stroke-linecap="round"/>
  <g transform="translate(138 146) rotate(-30)">
    <circle r="11" fill="#E0F2FE" fill-opacity=".6" stroke="#475569" stroke-width="3.5"/>
    <rect x="-3" y="10" width="6" height="16" rx="3" fill="#475569"/>
  </g>
""",
    # number auditor: tiny abacus-ish calculator
    "auditor": """
  <rect x="118" y="138" width="34" height="40" rx="6" fill="#334155" stroke="#0F172A" stroke-width="2.5"/>
  <rect x="123" y="143" width="24" height="9" rx="2" fill="#A7F3D0"/>
  <g fill="#E2E8F0">
    <rect x="123" y="156" width="6" height="5" rx="1.5"/><rect x="132" y="156" width="6" height="5" rx="1.5"/><rect x="141" y="156" width="6" height="5" rx="1.5"/>
    <rect x="123" y="165" width="6" height="5" rx="1.5"/><rect x="132" y="165" width="6" height="5" rx="1.5"/><rect x="141" y="165" width="6" height="5" rx="1.5"/>
  </g>
""",
}

HELMET = """
  <circle cx="100" cy="92" r="72" fill="#BAE6FD" fill-opacity=".16" stroke="#E0F2FE" stroke-width="4"/>
  <path d="M52 62 A58 58 0 0 1 96 32" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" opacity=".75"/>
  <rect x="58" y="158" width="84" height="16" rx="8" fill="#E2E8F0" stroke="#94A3B8" stroke-width="2.5"/>
"""


def svg(inner: str, view: str = "0 0 200 200") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" role="img" aria-label="Capy the capybara">{inner}</svg>\n'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "capy.svg").write_text(svg(BASE))
    for role, extra in ACCESSORIES.items():
        (OUT / f"capy-{role}.svg").write_text(svg(BASE + extra))
    (OUT / "capy-astronaut.svg").write_text(svg(BASE + HELMET, view="20 12 160 184"))
    print("wrote", sorted(p.name for p in OUT.glob("capy*.svg")))


if __name__ == "__main__":
    main()
