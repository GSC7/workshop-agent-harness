// Ícones SVG inline (mesmo traço e tamanho), sem dependência externa nem emoji.
interface PropsIcone {
  tamanho?: number;
}

const base = (tamanho: number) => ({
  width: tamanho,
  height: tamanho,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false,
});

export const IconeEnviar = ({ tamanho = 20 }: PropsIcone) => (
  <svg {...base(tamanho)}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export const IconeParar = ({ tamanho = 20 }: PropsIcone) => (
  <svg {...base(tamanho)}>
    <rect x="7" y="7" width="10" height="10" rx="1.5" />
  </svg>
);

export const IconeNovo = ({ tamanho = 18 }: PropsIcone) => (
  <svg {...base(tamanho)}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const IconeAlerta = ({ tamanho = 18 }: PropsIcone) => (
  <svg {...base(tamanho)}>
    <path d="M12 9v4M12 17h.01" />
    <path d="M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.7L13.7 3.9a2 2 0 0 0-3.4 0Z" />
  </svg>
);

export const IconeGrafico = ({ tamanho = 28 }: PropsIcone) => (
  <svg {...base(tamanho)}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </svg>
);
