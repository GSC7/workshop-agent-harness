import { Component, type ErrorInfo, type ReactNode } from "react";

interface Estado {
  falhou: boolean;
}

/**
 * Última barreira: erro de render inesperado mostra uma tela de recuperação, nunca branco.
 * (Error boundary só existe como classe no React — é a única classe do app.)
 */
export class LimiteDeErro extends Component<{ children: ReactNode }, Estado> {
  state: Estado = { falhou: false };

  static getDerivedStateFromError(): Estado {
    return { falhou: true };
  }

  componentDidCatch(erro: Error, info: ErrorInfo): void {
    console.error("falha de renderização:", erro, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.falhou) return this.props.children;
    return (
      <div className="erro" role="alert" style={{ margin: 24 }}>
        <div>
          <p className="erro-titulo">A tela encontrou um erro inesperado</p>
          <p>Recarregue a página para continuar. A conversa no servidor não foi perdida.</p>
        </div>
        <button type="button" className="botao secundario" onClick={() => window.location.reload()}>
          Recarregar
        </button>
      </div>
    );
  }
}
