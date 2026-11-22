const date = new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'});

export function WorkshopDate({value}: {value: number}) {
  const time = new Date(value * 1000);
  return <time dateTime={time.toISOString()}>{date.format(time)}</time>;
}
