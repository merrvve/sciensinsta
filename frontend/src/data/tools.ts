export interface ITool {
  id: number;
  title: string;
  description: string;
  limits: string;
  usage: string;
  link: string;
}

export const tools: ITool[] = [
  {
    id: 1,
    title: 'PDF Text & Image Extraction Tool',
    description: 'Extract text and images from PDF files easily, fast and free.',
    limits: 'Max 30 Mb file size',
    usage: 'Free',
    link: '/extract-pdf',
  },
  {
    id: 2,
    title: 'Text to .pptx Conversion Tool',
    description: 'We include three types of text to .pptx conversion tools.',
    limits: 'No character limit',
    usage: 'Free',
    link: '/text-to-pptx',
  },
  {
    id: 3,
    title: 'Pubmed Batch Abstract Tool',
    description: 'Download article abstracts from Pubmed in a structured table.',
    limits: 'Max 100 abstracts',
    usage: 'Free',
    link: '/pubmed-abstracts',
  },
  {
    id: 4,
    title: 'Categorise Articles (Coming Soon)',
    description: '',
    limits: '',
    usage: '',
    link: '',
  },
  {
    id: 5,
    title: 'PDF Table Extraction Tool (Coming Soon)',
    description: '',
    limits: '',
    usage: '',
    link: '',
  },
  {
    id: 6,
    title: 'Coming Soon',
    description: '',
    limits: '',
    usage: '',
    link: '',
  },
  {
    id: 7,
    title: 'Coming Soon',
    description: '',
    limits: '',
    usage: '',
    link: '',
  },
];
