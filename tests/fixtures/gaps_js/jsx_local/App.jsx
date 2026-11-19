const Card = () => <b>card</b>;
function Frame() { return <i>frame</i>; }
export function App() {
  const Styled = makeStyled();
  return <div><Card /><Frame /><Styled /></div>;
}
