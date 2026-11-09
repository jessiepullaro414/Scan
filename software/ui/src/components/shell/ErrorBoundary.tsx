import { Bug, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/Feedback";

type Props = { children: ReactNode; resetKey?: string; title?: string };
type State = { error: Error | null };

/** Keeps a crash on one page from blanking the app; offers a way out. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ohs ui: page crashed", error, info.componentStack);
  }

  override componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    const webgl = /webgl|context/i.test(error.message);
    return (
      <div className="grid h-full place-items-center p-6">
        <EmptyState
          tone="danger"
          icon={Bug}
          title={this.props.title ?? (webgl ? "The 3D view couldn't start" : "Something went wrong on this page")}
          actions={
            <>
              <Button variant="primary" icon={RotateCcw} onClick={() => this.setState({ error: null })}>
                Try again
              </Button>
              <Button variant="secondary" onClick={() => location.reload()}>
                Reload Studio
              </Button>
            </>
          }
        >
          <p>
            {webgl
              ? "WebGL is unavailable or the graphics driver reset. Update the graphics driver, or close other 3D apps, then try again."
              : "Your scans are safe on disk. Try again, or reload Studio."}
          </p>
          <p className="mt-2 font-mono text-xs text-subtle">{error.message}</p>
        </EmptyState>
      </div>
    );
  }
}
